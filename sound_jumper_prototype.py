import pygame
import numpy as np
import sounddevice as sd
import threading
import time
import random
import cv2
import mediapipe as mp

# ---------- 音频采样设置 ----------
SAMPLE_RATE = 44100
FRAME_SIZE = 1024

volume_rms = 0.0
input_gain = 1.0
lock = threading.Lock()

def audio_callback(indata, frames, time_info, status):
    global volume_rms
    if status:
        pass
    mono = np.mean(indata, axis=1) if indata.ndim > 1 else indata
    with lock:
        g = input_gain
    mono = mono * g
    rms = np.sqrt(np.mean(mono.astype(np.float64)**2))
    with lock:
        volume_rms = rms

def start_audio_stream():
    stream = sd.InputStream(channels=1, samplerate=SAMPLE_RATE,
                            blocksize=FRAME_SIZE, callback=audio_callback)
    stream.start()
    return stream

# ---------- MediaPipe 手势追踪设置 (新增) ----------
mp_hands = mp.solutions.hands
# max_num_hands=1 保证只追踪一只手，提高性能
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=1,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)
# 打开摄像头 (0 通常是默认摄像头)
cap = cv2.VideoCapture(0)

# ---------- Pygame 游戏设置 ----------
pygame.init()
pygame.mixer.init() 

WIDTH, HEIGHT = 480, 720
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Sound Jumper - Hand Control Ver.")

clock = pygame.time.Clock()
FONT = pygame.font.SysFont(None, 24)

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

# 音量 → 跳跃力 映射参数
VOLUME_THRESHOLD = 0.003
VOLUME_SENSITIVITY = 2000
BOUNCE_MULTIPLIER = 2.0 

# 平台
platforms = []
PLATFORM_WIDTH, PLATFORM_HEIGHT = 100, 12
NORMAL_PLATFORM_COLOR = (180, 180, 100)
BOUNCE_PLATFORM_COLOR = (255, 165, 0) 
BROKEN_PLATFORM_COLOR = (80, 80, 80) 

# 障碍物
hazards = []
HAZARD_COLOR = (255, 0, 0) 
HAZARD_SIZE = 10
HAZARD_SPEED = 3

def generate_initial_platforms():
    """Generates the starting platforms and resets the list."""
    global platforms
    platforms.clear()
    
    platforms.append((pygame.Rect(WIDTH // 2 - 50, HEIGHT - 100, 100, 12), False, False, False))
    y = HEIGHT - 250
    
    while y > -HEIGHT:
        x = random.randint(0, WIDTH - PLATFORM_WIDTH)
        is_bouncing = random.random() < 0.25
        platforms.append((pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT), is_bouncing, False, False))
        y -= random.randint(80, 120)

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
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

generate_initial_platforms()

# Game State
score = 0
is_jumping = False
scroll = 0
game_state = "START" 

# Settings / UI defaults
settings_open = False
settings_icon_rect = pygame.Rect(10, 10, 32, 32)
volume_bar_rect = pygame.Rect(10, 50, 200, 16)
settings_rect = pygame.Rect(WIDTH - 320, 10, 300, 80)
slider_rect = pygame.Rect(settings_rect.left + 16, settings_rect.top + 36, settings_rect.width - 32, 10)
slider_handle_radius = 8
dragging_slider = False

SPAWN_GRACE_DURATION = 0.5
spawn_grace_end = 0.0

MENU_BUTTONS = []
BUTTON_W, BUTTON_H = 220, 48
menu_top = HEIGHT//2 - 90
MENU_BUTTONS.append(("Start Game", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 0 * 60, BUTTON_W, BUTTON_H)))
MENU_BUTTONS.append(("Settings", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 1 * 60, BUTTON_W, BUTTON_H)))
MENU_BUTTONS.append(("Play Guide", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 2 * 60, BUTTON_W, BUTTON_H)))
MENU_BUTTONS.append(("Quit", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 3 * 60, BUTTON_W, BUTTON_H)))

show_guide = False
audio_stream = start_audio_stream()

# 用于平滑移动
hand_target_x = WIDTH // 2 

running = True
while running:
    # --- 摄像头手势处理 (每一帧都运行) ---
    success, image = cap.read()
    cam_surface = None
    
    if success:
        # 1. 镜像翻转 (让左手对应屏幕左边)
        image = cv2.flip(image, 1)
        # 2. 转换颜色空间 BGR -> RGB
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        # 3. 处理手部追踪
        results = hands.process(image_rgb)
        
        if results.multi_hand_landmarks:
            for hand_landmarks in results.multi_hand_landmarks:
                # 获取食指根部 (Index Finger MCP) 的 x 坐标 (范围 0.0 ~ 1.0)
                # 或者使用 hand_landmarks.landmark[9] (中指根部) 会更稳一点
                hand_x_percent = hand_landmarks.landmark[9].x
                
                # 映射到游戏屏幕宽度
                target_raw = hand_x_percent * WIDTH
                # 限制在屏幕内
                hand_target_x = max(0, min(WIDTH - player_w, target_raw - player_w/2))
                
                # 在摄像头画面上画个点提示
                h, w, c = image.shape
                cx, cy = int(hand_x_percent * w), int(hand_landmarks.landmark[9].y * h)
                cv2.circle(image, (cx, cy), 10, (0, 255, 0), -1)

        # 4. 生成用于UI显示的摄像头小窗口
        # 旋转图像以匹配Pygame坐标系，并缩放
        image_small = cv2.resize(image, (120, 90)) # 缩小尺寸
        image_small = cv2.cvtColor(image_small, cv2.COLOR_BGR2RGB)
        image_small = np.rot90(image_small) # OpenCV是行主序，Pygame需要转换
        image_small = pygame.surfarray.make_surface(image_small)
        cam_surface = pygame.transform.flip(image_small, True, False) # 再次修正方向


    # --- Event Handling ---
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos
            if show_guide:
                show_guide = False
                continue
            if game_state == "START":
                for label, rect in MENU_BUTTONS:
                    if rect.collidepoint(mx, my):
                        if label == "Start Game":
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
                            except Exception:
                                player_x = WIDTH//2 - player_w//2
                                player_y = HEIGHT - 200
                            spawn_grace_end = time.time() + SPAWN_GRACE_DURATION
                            game_state = "PLAYING"
                        elif label == "Settings":
                            settings_open = True
                        elif label == "Play Guide":
                            show_guide = True
                        elif label == "Quit":
                            running = False
                        break
            if settings_icon_rect.collidepoint(mx, my):
                settings_open = not settings_open
            if volume_bar_rect.collidepoint(mx, my):
                rel = (mx - volume_bar_rect.left) / volume_bar_rect.width
                rel = max(0.0, min(1.0, rel))
                with lock: input_gain = rel * 5.0
                dragging_slider = True
            if settings_open and slider_rect.collidepoint(mx, my):
                dragging_slider = True

        if event.type == pygame.MOUSEBUTTONUP:
            dragging_slider = False

        if event.type == pygame.MOUSEMOTION and dragging_slider:
            mx, my = event.pos
            if settings_open:
                left, width = slider_rect.left, slider_rect.width
            else:
                left, width = volume_bar_rect.left, volume_bar_rect.width
            rel = (mx - left) / width
            rel = max(0.0, min(1.0, rel))
            with lock: input_gain = rel * 5.0

        if event.type == pygame.KEYDOWN:
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
                except Exception:
                    player_x = WIDTH//2 - player_w//2
                    player_y = HEIGHT - 200
                spawn_grace_end = time.time() + SPAWN_GRACE_DURATION
                game_state = "PLAYING"

    # --- State Machine ---
    if game_state == "START":
        screen.fill((20, 24, 30))
        title_font = pygame.font.SysFont(None, 72)
        title_text = title_font.render("Sound Jumper", True, (220, 220, 255))
        screen.blit(title_text, (WIDTH//2 - title_text.get_width()//2, HEIGHT//6))

        for label, rect in MENU_BUTTONS:
            pygame.draw.rect(screen, (40, 40, 60), rect)
            pygame.draw.rect(screen, (120, 120, 160), rect, 2)
            text_surf = FONT.render(label, True, (220, 220, 255))
            screen.blit(text_surf, (rect.left + rect.width//2 - text_surf.get_width()//2, rect.top + rect.height//2 - text_surf.get_height()//2))

        if show_guide:
            guide_rect = pygame.Rect(40, 80, WIDTH - 80, HEIGHT - 160)
            pygame.draw.rect(screen, (18, 18, 24), guide_rect)
            pygame.draw.rect(screen, (100, 100, 140), guide_rect, 2)
            guide_text = (
                "How to play:\n"
                "MOVE YOUR HAND left/right to move the character.\n" # Updated Text
                "Make noise into your mic to jump.\n"
                "Orange platforms bounce; Grey ones break (30% chance).\n\n" # Updated Text
                "Click anywhere to go back."
            )
            margin = 12
            max_w = guide_rect.width - margin * 2
            y = guide_rect.top + margin
            for paragraph in guide_text.split('\n'):
                if paragraph.strip() == "":
                    y += FONT.get_height() // 2
                    continue
                lines = wrap_text(paragraph, FONT, max_w)
                for line in lines:
                    surf = FONT.render(line, True, (200, 200, 255))
                    screen.blit(surf, (guide_rect.left + margin, y))
                    y += surf.get_height() + 6

    elif game_state == "PLAYING":
        # --- 修改点：使用手势坐标代替键盘输入 ---
        # 使用线性插值 (Lerp) 让移动更平滑，而不是瞬间跳跃
        player_x += (hand_target_x - player_x) * 0.2 

        # 也可以保留键盘作为辅助调试
        keys = pygame.key.get_pressed()
        if keys[pygame.K_a] or keys[pygame.K_LEFT]: player_x -= speed
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]: player_x += speed

        with lock:
            current_rms = volume_rms

        paused = settings_open

        if not paused:
            jump_force = 0.0
            if current_rms > VOLUME_THRESHOLD:
                jump_force = (current_rms - VOLUME_THRESHOLD) * VOLUME_SENSITIVITY
                if jump_force > 18:
                    jump_force = 18

            # --- Physics Update ---
            # player_x logic moved to above (hand tracking)
            player_y += velocity_y
            
            player_rect = pygame.Rect(int(player_x), int(player_y), player_w, player_h)
            
            standing_on_platform = None
            is_on_bouncy_platform = False
            
            if velocity_y >= 0:
                new_platforms = []
                platform_landed_index = -1
                
                for i, (plat_rect, is_bouncing, is_broken, is_falling) in enumerate(platforms):
                    
                    if not is_falling and player_rect.colliderect(plat_rect) and abs(player_rect.bottom - plat_rect.top) < velocity_y + 1:
                        standing_on_platform = plat_rect
                        is_on_bouncy_platform = is_bouncing 
                        platform_landed_index = i 

                        # --- 修改点：30% 概率随机破碎 ---
                        if not is_bouncing and i != 0:
                            # 如果是普通平台，掷骰子决定是否破碎
                            if random.random() < 0.30: # 30% 概率
                                platforms[i] = (plat_rect, is_bouncing, True, True) # 标记破碎
                        
                        break
                
                if standing_on_platform:
                    player_y = standing_on_platform.top - player_h
                    velocity_y = 0
                    is_jumping = False
                else:
                    velocity_y += gravity
            else:
                velocity_y += gravity

            base_jump_vel = - (6 + jump_force)
            
            if standing_on_platform and jump_force > 1.0 and not is_jumping:
                if is_on_bouncy_platform:
                    velocity_y = base_jump_vel * BOUNCE_MULTIPLIER
                else:
                    velocity_y = base_jump_vel
                    
                is_jumping = True
            
            if standing_on_platform and is_on_bouncy_platform and not is_jumping and jump_force < 1.0:
                 velocity_y = - (12) 
                 is_jumping = True

            for hazard_rect, _ in hazards:
                if player_rect.colliderect(hazard_rect):
                    if time.time() >= spawn_grace_end:
                        game_state = "GAME_OVER"
                        break
            
            if game_state == "GAME_OVER":
                continue

            if player_y < HEIGHT / 2.5:
                scroll_amount = (HEIGHT / 2.5) - player_y
                player_y += scroll_amount
                scroll += scroll_amount
                
                new_platforms = []
                highest_platform_y = HEIGHT
                
                for plat_rect, is_bouncing, is_broken, is_falling in platforms:
                    if is_falling:
                        plat_rect.y += PLATFORM_FALL_SPEED 
                    else:
                        plat_rect.y += scroll_amount 

                    if plat_rect.bottom > 0:
                        new_platforms.append((plat_rect, is_bouncing, is_broken, is_falling))
                        if not is_falling and plat_rect.y < highest_platform_y:
                            highest_platform_y = plat_rect.y
                
                platforms = new_platforms

                new_hazards = []
                for hazard_rect, vx in hazards:
                    hazard_rect.y += scroll_amount
                    if hazard_rect.bottom > 0:
                        new_hazards.append((hazard_rect, vx))
                hazards = new_hazards
                
                if len(platforms) < 10 or highest_platform_y > 0: 
                    y = highest_platform_y
                    while y > -HEIGHT:
                        y -= random.randint(80, 120)
                        x = random.randint(0, WIDTH - PLATFORM_WIDTH)
                        is_bouncing = random.random() < 0.25
                        platforms.append((pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT), is_bouncing, False, False))
                    
                    if random.random() < 0.5: generate_hazard(highest_platform_y)
                    if random.random() < 0.2: generate_hazard(highest_platform_y - 200)

            score = int(scroll / 10)

            for i, (hazard_rect, vx) in enumerate(hazards):
                hazard_rect.x += vx
                if hazard_rect.left < 0 or hazard_rect.right > WIDTH:
                    vx = -vx
                    hazards[i] = (hazard_rect, vx)

            if player_x < -player_w: player_x = WIDTH
            elif player_x > WIDTH: player_x = -player_w
            
            if player_y > HEIGHT and time.time() >= spawn_grace_end:
                game_state = "GAME_OVER"

        # --- Rendering ---
        screen.fill((20, 24, 30))
        
        for plat_rect, is_bouncing, is_broken, is_falling in platforms:
            if is_falling: color = BROKEN_PLATFORM_COLOR
            else: color = BOUNCE_PLATFORM_COLOR if is_bouncing else NORMAL_PLATFORM_COLOR
            pygame.draw.rect(screen, color, plat_rect)

        for hazard_rect, _ in hazards:
            pygame.draw.circle(screen, HAZARD_COLOR, hazard_rect.center, HAZARD_SIZE // 2)

        pygame.draw.rect(screen, (200, 80, 120), (int(player_x), int(player_y), player_w, player_h))
        
        vol_pct = min(1.0, current_rms / 0.02)
        pygame.draw.rect(screen, (40,40,40), (10, 10, 200, 16))
        pygame.draw.rect(screen, (80, 200, 120), (10, 10, int(200 * vol_pct), 16))
        score_text = FONT.render(f"Score: {score}", True, (200,200,200))
        screen.blit(score_text, (WIDTH - score_text.get_width() - 10, 10))
        
        # Draw settings icon
        pygame.draw.rect(screen, (30, 30, 40), settings_icon_rect)
        pygame.draw.circle(screen, (200, 200, 200), settings_icon_rect.center, 12, 2)
        
        with lock: g_display = input_gain
        handle_x_bar = int(volume_bar_rect.left + (g_display / 5.0) * volume_bar_rect.width)
        pygame.draw.circle(screen, (200,200,160), (handle_x_bar, volume_bar_rect.centery), slider_handle_radius)

        if settings_open:
            pygame.draw.rect(screen, (20,20,30), settings_rect)
            pygame.draw.rect(screen, (60,60,70), slider_rect)
            handle_x = int(slider_rect.left + (g_display / 5.0) * slider_rect.width)
            pygame.draw.circle(screen, (180, 220, 200), (handle_x, slider_rect.centery), slider_handle_radius)
            screen.blit(FONT.render(f"Input Gain: {g_display:.2f}x", True, (220,220,255)), (slider_rect.left, slider_rect.top - 22))

    elif game_state == "GAME_OVER":
        screen.fill((20, 24, 30))
        title_font = pygame.font.SysFont(None, 72)
        score_font = pygame.font.SysFont(None, 48)
        info_font = pygame.font.SysFont(None, 36)
        title_text = title_font.render("Game Over", True, (200, 80, 120))
        score_text = score_font.render(f"Final Score: {score}", True, (200, 200, 200))
        info_text = info_font.render("Press any key to play again", True, (150, 150, 150))
        screen.blit(title_text, (WIDTH//2 - title_text.get_width()//2, HEIGHT//4))
        screen.blit(score_text, (WIDTH//2 - score_text.get_width()//2, HEIGHT//2 - 50))
        screen.blit(info_text, (WIDTH//2 - info_text.get_width()//2, HEIGHT//2 + 20))
    
    # --- 画摄像头缩略图 ---
    if cam_surface:
        screen.blit(cam_surface, (WIDTH - 130, HEIGHT - 100))
        pygame.draw.rect(screen, (100,255,100), (WIDTH - 130, HEIGHT - 100, 120, 90), 1)

    pygame.display.flip()
    clock.tick(60)

audio_stream.stop()
audio_stream.close()
cap.release() # 释放摄像头
pygame.quit()