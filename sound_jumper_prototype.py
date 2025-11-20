import pygame
import numpy as np
import sounddevice as sd
import threading
import time
import random
import cv2
import mediapipe as mp

# ---------- 1. 初始化 Pygame 以获取屏幕尺寸 ----------
pygame.init()
pygame.mixer.init()

# 获取当前显示器的分辨率
info = pygame.display.Info()
WIDTH, HEIGHT = info.current_w, info.current_h

# 设置为全屏模式
screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.FULLSCREEN)
pygame.display.set_caption("Sound Jumper - Immersive Mode")

# ---------- 音频采样设置 ----------
SAMPLE_RATE = 44100
FRAME_SIZE = 1024

volume_rms = 0.0
input_gain = 1.0
lock = threading.Lock()

def audio_callback(indata, frames, time_info, status):
    global volume_rms
    if status: pass
    mono = np.mean(indata, axis=1) if indata.ndim > 1 else indata
    with lock: g = input_gain
    mono = mono * g
    rms = np.sqrt(np.mean(mono.astype(np.float64)**2))
    with lock: volume_rms = rms

def start_audio_stream():
    stream = sd.InputStream(channels=1, samplerate=SAMPLE_RATE,
                            blocksize=FRAME_SIZE, callback=audio_callback)
    stream.start()
    return stream

# ---------- MediaPipe 手势追踪设置 ----------
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=1,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)
cap = cv2.VideoCapture(0)

# ---------- 游戏参数设置 ----------
clock = pygame.time.Clock()
FONT = pygame.font.SysFont(None, 30) # 稍微调大字体

# 角色
player_w, player_h = 40, 40
player_x = WIDTH//2 - player_w//2
player_y = HEIGHT - 200
player_vx = 0
velocity_y = 0
speed = 5

# 物理
gravity = 0.6
PLATFORM_FALL_SPEED = 10 

# 音量 → 跳跃力
VOLUME_THRESHOLD = 0.003
VOLUME_SENSITIVITY = 2000
BOUNCE_MULTIPLIER = 2.0 

# 平台 (稍微加宽平台以适应宽屏)
platforms = []
PLATFORM_WIDTH, PLATFORM_HEIGHT = 120, 15 
NORMAL_PLATFORM_COLOR = (180, 180, 100)
BOUNCE_PLATFORM_COLOR = (255, 165, 0) 
BROKEN_PLATFORM_COLOR = (80, 80, 80) 

# 障碍物
hazards = []
HAZARD_COLOR = (255, 50, 50) 
HAZARD_SIZE = 15
HAZARD_SPEED = 4 # 稍微加快

def generate_initial_platforms():
    global platforms
    platforms.clear()
    # 初始平台
    platforms.append((pygame.Rect(WIDTH // 2 - 50, HEIGHT - 150, 100, 15), False, False, False))
    y = HEIGHT - 300
    while y > -HEIGHT:
        # 让平台分布更广一些
        x = random.randint(0, WIDTH - PLATFORM_WIDTH)
        is_bouncing = random.random() < 0.25
        platforms.append((pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT), is_bouncing, False, False))
        y -= random.randint(80, 140)

def generate_hazard(highest_y):
    x = random.randint(0, WIDTH)
    y = highest_y - random.randint(100, 300) 
    vx = random.choice([-HAZARD_SPEED, HAZARD_SPEED]) 
    hazards.append((pygame.Rect(x, y, HAZARD_SIZE, HAZARD_SIZE), vx))

def wrap_text(text, font, max_width):
    words = text.split(' ')
    lines = []
    cur = ""
    for w in words:
        test = cur + (" " if cur else "") + w
        if font.size(test)[0] <= max_width:
            cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines

generate_initial_platforms()

# Game State
score = 0
is_jumping = False
scroll = 0
game_state = "START" 

# Settings / UI
settings_open = False
settings_icon_rect = pygame.Rect(20, 20, 40, 40)
volume_bar_rect = pygame.Rect(20, 70, 250, 20)
settings_rect = pygame.Rect(WIDTH - 350, 20, 330, 100)
slider_rect = pygame.Rect(settings_rect.left + 20, settings_rect.top + 50, settings_rect.width - 40, 10)
slider_handle_radius = 10
dragging_slider = False

SPAWN_GRACE_DURATION = 0.5
spawn_grace_end = 0.0

MENU_BUTTONS = []
BUTTON_W, BUTTON_H = 260, 60
menu_top = HEIGHT//2 - 60
MENU_BUTTONS.append(("Start Game", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 0 * 80, BUTTON_W, BUTTON_H)))
MENU_BUTTONS.append(("Settings", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 1 * 80, BUTTON_W, BUTTON_H)))
MENU_BUTTONS.append(("Play Guide", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 2 * 80, BUTTON_W, BUTTON_H)))
MENU_BUTTONS.append(("Quit (ESC)", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 3 * 80, BUTTON_W, BUTTON_H)))

show_guide = False
audio_stream = start_audio_stream()

# 预创建一个半透明黑色遮罩层，用于压暗背景
dim_surface = pygame.Surface((WIDTH, HEIGHT))
dim_surface.set_alpha(150) # 透明度 0-255，越大越黑
dim_surface.fill((0, 0, 0))

hand_target_x = WIDTH // 2 

running = True
while running:
    # --- 1. 摄像头背景处理 ---
    success, image = cap.read()
    bg_surface = None
    
    if success:
        # 镜像 & 转 RGB
        image = cv2.flip(image, 1)
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        
        # 手势追踪
        results = hands.process(image_rgb)
        if results.multi_hand_landmarks:
            for hand_landmarks in results.multi_hand_landmarks:
                # 使用中指根部作为基准点
                hand_x_percent = hand_landmarks.landmark[9].x
                target_raw = hand_x_percent * WIDTH
                hand_target_x = max(0, min(WIDTH - player_w, target_raw - player_w/2))
                
                # 在背景图上画个绿点 (可选)
                h, w, c = image.shape
                cx, cy = int(hand_x_percent * w), int(hand_landmarks.landmark[9].y * h)
                cv2.circle(image, (cx, cy), 15, (0, 255, 0), -1)

        # 准备背景图：旋转并拉伸到全屏
        # OpenCV 图片是 (Height, Width)，Pygame 需要 (Width, Height)
        # 直接 Resize 到屏幕分辨率
        bg_image = cv2.resize(image, (WIDTH, HEIGHT))
        bg_image = cv2.cvtColor(bg_image, cv2.COLOR_BGR2RGB)
        # 转为 Pygame Surface
        bg_surface = pygame.image.frombuffer(bg_image.tobytes(), bg_image.shape[1::-1], "RGB")

    # --- 绘制背景 ---
    if bg_surface:
        screen.blit(bg_surface, (0, 0))
    else:
        screen.fill((20, 24, 30)) # 摄像头失败时的备用色
        
    # 绘制遮罩层 (让背景变暗，突显游戏)
    screen.blit(dim_surface, (0, 0))

    # --- Event Handling ---
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE: # ESC 退出
                running = False
            
            if show_guide: show_guide = False; continue
            if settings_open: settings_open = False
            elif (game_state == "START" or game_state == "GAME_OVER"):
                player_vx = velocity_y = 0
                score = 0
                scroll = 0
                is_jumping = False
                generate_initial_platforms()
                hazards.clear()
                try:
                    init_plat = platforms[0][0]
                    player_x = init_plat.left + (init_plat.width - player_w) / 2
                    player_y = init_plat.top - player_h
                except:
                    player_x = WIDTH//2 - player_w//2
                    player_y = HEIGHT - 200
                spawn_grace_end = time.time() + SPAWN_GRACE_DURATION
                game_state = "PLAYING"

        if event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos
            if show_guide: show_guide = False; continue
            
            if game_state == "START":
                for label, rect in MENU_BUTTONS:
                    if rect.collidepoint(mx, my):
                        if label == "Start Game":
                            # Reset logic... (same as above)
                            player_vx = velocity_y = 0
                            score = 0
                            scroll = 0
                            is_jumping = False
                            generate_initial_platforms()
                            hazards.clear()
                            try:
                                init_plat = platforms[0][0]
                                player_x = init_plat.left + (init_plat.width - player_w) / 2
                                player_y = init_plat.top - player_h
                            except:
                                player_x = WIDTH//2 - player_w//2
                                player_y = HEIGHT - 200
                            spawn_grace_end = time.time() + SPAWN_GRACE_DURATION
                            game_state = "PLAYING"
                        elif label == "Settings": settings_open = True
                        elif label == "Play Guide": show_guide = True
                        elif "Quit" in label: running = False
            
            if settings_icon_rect.collidepoint(mx, my): settings_open = not settings_open
            if volume_bar_rect.collidepoint(mx, my):
                rel = (mx - volume_bar_rect.left) / volume_bar_rect.width
                rel = max(0.0, min(1.0, rel))
                with lock: input_gain = rel * 5.0
                dragging_slider = True
            if settings_open and slider_rect.collidepoint(mx, my): dragging_slider = True

        if event.type == pygame.MOUSEBUTTONUP: dragging_slider = False
        if event.type == pygame.MOUSEMOTION and dragging_slider:
            mx, my = event.pos
            if settings_open: left, width = slider_rect.left, slider_rect.width
            else: left, width = volume_bar_rect.left, volume_bar_rect.width
            rel = (mx - left) / width
            rel = max(0.0, min(1.0, rel))
            with lock: input_gain = rel * 5.0

    # --- State Machine ---
    if game_state == "START":
        title_font = pygame.font.SysFont(None, 100)
        title_text = title_font.render("Sound Jumper", True, (255, 255, 255))
        # 标题加个阴影让它在视频背景上更清晰
        title_shadow = title_font.render("Sound Jumper", True, (0, 0, 0))
        screen.blit(title_shadow, (WIDTH//2 - title_text.get_width()//2 + 4, HEIGHT//6 + 4))
        screen.blit(title_text, (WIDTH//2 - title_text.get_width()//2, HEIGHT//6))

        for label, rect in MENU_BUTTONS:
            # 半透明按钮背景
            s = pygame.Surface((rect.width, rect.height))
            s.set_alpha(200)
            s.fill((40, 40, 60))
            screen.blit(s, (rect.x, rect.y))
            
            pygame.draw.rect(screen, (150, 150, 200), rect, 2)
            text_surf = FONT.render(label, True, (220, 220, 255))
            screen.blit(text_surf, (rect.left + rect.width//2 - text_surf.get_width()//2, rect.top + rect.height//2 - text_surf.get_height()//2))

        if show_guide:
            guide_rect = pygame.Rect(WIDTH//2 - 300, HEIGHT//2 - 200, 600, 400)
            s = pygame.Surface((guide_rect.width, guide_rect.height))
            s.set_alpha(240)
            s.fill((20, 20, 30))
            screen.blit(s, (guide_rect.x, guide_rect.y))
            pygame.draw.rect(screen, (100, 100, 140), guide_rect, 2)
            
            guide_text = (
                "How to play:\n"
                "1. MOVE YOUR HAND left/right to move.\n"
                "2. SCREAM / CLAP to jump.\n"
                "3. Orange platforms bounce.\n"
                "4. Grey platforms break (30%).\n\n"
                "Click to back."
            )
            y = guide_rect.top + 40
            for line in guide_text.split('\n'):
                surf = FONT.render(line, True, (255, 255, 255))
                screen.blit(surf, (guide_rect.left + 30, y))
                y += 40

    elif game_state == "PLAYING":
        # Lerp Movement
        player_x += (hand_target_x - player_x) * 0.2 

        with lock: current_rms = volume_rms
        paused = settings_open

        if not paused:
            jump_force = 0.0
            if current_rms > VOLUME_THRESHOLD:
                jump_force = (current_rms - VOLUME_THRESHOLD) * VOLUME_SENSITIVITY
                if jump_force > 18: jump_force = 18

            player_y += velocity_y
            player_rect = pygame.Rect(int(player_x), int(player_y), player_w, player_h)
            
            standing_on_platform = None
            is_on_bouncy_platform = False
            
            if velocity_y >= 0:
                new_platforms = []
                for i, (plat_rect, is_bouncing, is_broken, is_falling) in enumerate(platforms):
                    if not is_falling and player_rect.colliderect(plat_rect) and abs(player_rect.bottom - plat_rect.top) < velocity_y + 5: # 增加判定宽容度
                        standing_on_platform = plat_rect
                        is_on_bouncy_platform = is_bouncing 
                        if not is_bouncing and i != 0 and random.random() < 0.30:
                             platforms[i] = (plat_rect, is_bouncing, True, True)
                        break
                
                if standing_on_platform:
                    player_y = standing_on_platform.top - player_h
                    velocity_y = 0
                    is_jumping = False
                else:
                    velocity_y += gravity
            else:
                velocity_y += gravity

            base_jump_vel = - (8 + jump_force) # 稍微增加基础跳跃高度适应大屏
            
            if standing_on_platform and jump_force > 1.0 and not is_jumping:
                velocity_y = base_jump_vel * BOUNCE_MULTIPLIER if is_on_bouncy_platform else base_jump_vel
                is_jumping = True
            
            if standing_on_platform and is_on_bouncy_platform and not is_jumping and jump_force < 1.0:
                 velocity_y = -15 
                 is_jumping = True

            # Hazard Collision
            for hazard_rect, _ in hazards:
                if player_rect.colliderect(hazard_rect):
                    if time.time() >= spawn_grace_end:
                        game_state = "GAME_OVER"
                        break
            
            if game_state == "GAME_OVER": continue

            # Scroll Logic
            if player_y < HEIGHT / 2.5:
                scroll_amount = (HEIGHT / 2.5) - player_y
                player_y += scroll_amount
                scroll += scroll_amount
                
                new_platforms = []
                highest_platform_y = HEIGHT
                for plat_rect, is_bouncing, is_broken, is_falling in platforms:
                    if is_falling: plat_rect.y += PLATFORM_FALL_SPEED 
                    else: plat_rect.y += scroll_amount 
                    if plat_rect.bottom > 0:
                        new_platforms.append((plat_rect, is_bouncing, is_broken, is_falling))
                        if not is_falling and plat_rect.y < highest_platform_y: highest_platform_y = plat_rect.y
                platforms = new_platforms

                new_hazards = []
                for hazard_rect, vx in hazards:
                    hazard_rect.y += scroll_amount
                    if hazard_rect.bottom > 0: new_hazards.append((hazard_rect, vx))
                hazards = new_hazards
                
                if len(platforms) < 15 or highest_platform_y > 0: 
                    y = highest_platform_y
                    while y > -HEIGHT:
                        y -= random.randint(100, 180) # 增加间距
                        x = random.randint(0, WIDTH - PLATFORM_WIDTH)
                        is_bouncing = random.random() < 0.25
                        platforms.append((pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT), is_bouncing, False, False))
                    
                    if random.random() < 0.5: generate_hazard(highest_platform_y)

            score = int(scroll / 10)
            for i, (hazard_rect, vx) in enumerate(hazards):
                hazard_rect.x += vx
                if hazard_rect.left < 0 or hazard_rect.right > WIDTH:
                    vx = -vx
                    hazards[i] = (hazard_rect, vx)

            if player_x < -player_w: player_x = WIDTH
            elif player_x > WIDTH: player_x = -player_w
            if player_y > HEIGHT and time.time() >= spawn_grace_end: game_state = "GAME_OVER"

        # --- Rendering Game Elements ---
        # 不再 fill 背景，直接在半透明遮罩上画
        
        for plat_rect, is_bouncing, is_broken, is_falling in platforms:
            if is_falling: color = BROKEN_PLATFORM_COLOR
            else: color = BOUNCE_PLATFORM_COLOR if is_bouncing else NORMAL_PLATFORM_COLOR
            pygame.draw.rect(screen, color, plat_rect)

        for hazard_rect, _ in hazards:
            pygame.draw.circle(screen, HAZARD_COLOR, hazard_rect.center, HAZARD_SIZE // 2)

        # 画玩家
        pygame.draw.rect(screen, (200, 80, 120), (int(player_x), int(player_y), player_w, player_h))
        
        # UI
        vol_pct = min(1.0, current_rms / 0.02)
        pygame.draw.rect(screen, (40,40,40), volume_bar_rect)
        pygame.draw.rect(screen, (80, 200, 120), (volume_bar_rect.x, volume_bar_rect.y, int(volume_bar_rect.width * vol_pct), volume_bar_rect.height))
        
        score_text = FONT.render(f"Score: {score}", True, (255,255,255))
        screen.blit(score_text, (WIDTH - score_text.get_width() - 30, 20))
        
        pygame.draw.rect(screen, (50, 50, 60), settings_icon_rect)
        pygame.draw.circle(screen, (200, 200, 200), settings_icon_rect.center, 12, 2)
        
        if settings_open:
            s = pygame.Surface((settings_rect.width, settings_rect.height))
            s.set_alpha(230)
            s.fill((20,20,30))
            screen.blit(s, settings_rect)
            
            with lock: g_display = input_gain
            pygame.draw.rect(screen, (60,60,70), slider_rect)
            handle_x = int(slider_rect.left + (g_display / 5.0) * slider_rect.width)
            pygame.draw.circle(screen, (180, 220, 200), (handle_x, slider_rect.centery), slider_handle_radius)
            screen.blit(FONT.render(f"Gain: {g_display:.2f}x", True, (220,220,255)), (slider_rect.left, slider_rect.top - 25))

    elif game_state == "GAME_OVER":
        title_font = pygame.font.SysFont(None, 100)
        score_font = pygame.font.SysFont(None, 60)
        info_font = pygame.font.SysFont(None, 40)
        
        title_text = title_font.render("Game Over", True, (255, 100, 100))
        score_text = score_font.render(f"Final Score: {score}", True, (255, 255, 255))
        info_text = info_font.render("Press any key to restart", True, (200, 200, 200))

        screen.blit(title_text, (WIDTH//2 - title_text.get_width()//2, HEIGHT//3))
        screen.blit(score_text, (WIDTH//2 - score_text.get_width()//2, HEIGHT//2))
        screen.blit(info_text, (WIDTH//2 - info_text.get_width()//2, HEIGHT//2 + 80))

    pygame.display.flip()
    clock.tick(60)

audio_stream.stop()
audio_stream.close()
cap.release()
pygame.quit()