import pygame
import numpy as np
import sounddevice as sd
import threading
import time
import random

# ---------- 音频采样设置 ----------
SAMPLE_RATE = 44100
FRAME_SIZE = 1024

volume_rms = 0.0
# Input gain (multiplier applied to incoming audio). Range 0.0 .. 5.0
input_gain = 1.0
lock = threading.Lock()

def audio_callback(indata, frames, time_info, status):
    global volume_rms
    if status:
        # ignore status messages for now
        pass
    # Convert to mono
    mono = np.mean(indata, axis=1) if indata.ndim > 1 else indata
    # Read gain in a thread-safe way and apply
    with lock:
        g = input_gain
    mono = mono * g
    # Compute RMS (root mean square)
    rms = np.sqrt(np.mean(mono.astype(np.float64)**2))
    with lock:
        volume_rms = rms

def start_audio_stream():
    stream = sd.InputStream(channels=1, samplerate=SAMPLE_RATE,
                            blocksize=FRAME_SIZE, callback=audio_callback)
    stream.start()
    return stream

# ---------- Pygame 游戏设置 ----------
pygame.init()
# 初始化 Pygame 混音器（为未来的音效做准备）
pygame.mixer.init() 

WIDTH, HEIGHT = 480, 720
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Sound Jumper - Endless")

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
PLATFORM_FALL_SPEED = 10 # 平台破碎后的下坠速度

# 音量 → 跳跃力 映射参数
VOLUME_THRESHOLD = 0.003
VOLUME_SENSITIVITY = 2000
BOUNCE_MULTIPLIER = 2.0 # 弹跳平台修正系数

# 平台
platforms = []
PLATFORM_WIDTH, PLATFORM_HEIGHT = 100, 12
NORMAL_PLATFORM_COLOR = (180, 180, 100)
BOUNCE_PLATFORM_COLOR = (255, 165, 0) # 橙色
BROKEN_PLATFORM_COLOR = (80, 80, 80) # 灰色

# 障碍物
hazards = []
HAZARD_COLOR = (255, 0, 0) # 红色
HAZARD_SIZE = 10
HAZARD_SPEED = 3

def generate_initial_platforms():
    """Generates the starting platforms and resets the list."""
    global platforms
    platforms.clear()
    
    # 平台结构: (pygame.Rect, is_bouncing: bool, is_broken: bool, is_falling: bool)
    # 初始平台
    platforms.append((pygame.Rect(WIDTH // 2 - 50, HEIGHT - 100, 100, 12), False, False, False))
    y = HEIGHT - 250
    
    while y > -HEIGHT:
        x = random.randint(0, WIDTH - PLATFORM_WIDTH)
        # 随机决定是否为弹跳平台 (25% 的概率)
        is_bouncing = random.random() < 0.25
        # 存储为元组 (Rect, is_bouncing, is_broken=False, is_falling=False)
        platforms.append((pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT), is_bouncing, False, False))
        y -= random.randint(80, 120)

def generate_hazard(highest_y):
    """Generates a new hazard particle above the highest platform."""
    # 随机生成在屏幕宽度内，并在最高平台y坐标上方随机位置
    x = random.randint(0, WIDTH)
    y = highest_y - random.randint(100, 300) 
    
    # 障碍物结构: (pygame.Rect, velocity_x: float)
    vx = random.choice([-HAZARD_SPEED, HAZARD_SPEED]) # 随机左右移动
    hazards.append((pygame.Rect(x, y, HAZARD_SIZE, HAZARD_SIZE), vx))

def wrap_text(text, font, max_width):
    """Simple word-wrap: returns a list of lines that fit within max_width (pixels)."""
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
game_state = "START" # Can be "START", "PLAYING", "GAME_OVER"

# Settings / UI defaults for input gain control
settings_open = False
settings_icon_rect = pygame.Rect(10, 10, 32, 32)
volume_bar_rect = pygame.Rect(10, 50, 200, 16)
settings_rect = pygame.Rect(WIDTH - 320, 10, 300, 80)
slider_rect = pygame.Rect(settings_rect.left + 16, settings_rect.top + 36, settings_rect.width - 32, 10)
slider_handle_radius = 8
dragging_slider = False
# Short grace time after spawning where the player cannot die (seconds)
SPAWN_GRACE_DURATION = 0.5
spawn_grace_end = 0.0
# Start menu UI
MENU_BUTTONS = []
BUTTON_W, BUTTON_H = 220, 48
menu_top = HEIGHT//2 - 90
MENU_BUTTONS.append(("Start Game", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 0 * 60, BUTTON_W, BUTTON_H)))
MENU_BUTTONS.append(("Settings", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 1 * 60, BUTTON_W, BUTTON_H)))
MENU_BUTTONS.append(("Play Guide", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 2 * 60, BUTTON_W, BUTTON_H)))
MENU_BUTTONS.append(("Quit", pygame.Rect(WIDTH//2 - BUTTON_W//2, menu_top + 3 * 60, BUTTON_W, BUTTON_H)))
# Guide screen flag
show_guide = False

# 启动音频线程/流
audio_stream = start_audio_stream()

running = True
while running:
    # --- Event Handling (common to all states) ---
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        # Mouse controls for settings / gain
        if event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos
            # Close guide if it's open and user clicks
            if show_guide:
                show_guide = False
                continue
            # If on START screen, handle menu button clicks
            if game_state == "START":
                for label, rect in MENU_BUTTONS:
                    if rect.collidepoint(mx, my):
                        if label == "Start Game":
                            # Start the game (simulate keydown start behavior)
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
            # Toggle settings icon if clicked (top-left)
            try:
                if settings_icon_rect.collidepoint(mx, my):
                    settings_open = not settings_open
            except NameError:
                # settings not yet initialized; initialize defaults now
                settings_open = False
                settings_icon_rect = pygame.Rect(10, 10, 32, 32)
                volume_bar_rect = pygame.Rect(10, 50, 200, 16)
                settings_rect = pygame.Rect(WIDTH - 320, 10, 300, 80)
                slider_rect = pygame.Rect(settings_rect.left + 16, settings_rect.top + 36, settings_rect.width - 32, 10)
                slider_handle_radius = 8
                dragging_slider = False
                if settings_icon_rect.collidepoint(mx, my):
                    settings_open = not settings_open
            # Click on the RMS HUD bar to set gain quickly
            if 'volume_bar_rect' in globals() and volume_bar_rect.collidepoint(mx, my):
                rel = (mx - volume_bar_rect.left) / volume_bar_rect.width
                rel = max(0.0, min(1.0, rel))
                with lock:
                    input_gain = rel * 5.0
                dragging_slider = True
            # Click inside settings slider to begin dragging
            if 'settings_open' in globals() and settings_open and slider_rect.collidepoint(mx, my):
                dragging_slider = True

        if event.type == pygame.MOUSEBUTTONUP:
            dragging_slider = False

        if event.type == pygame.MOUSEMOTION and 'dragging_slider' in globals() and dragging_slider:
            mx, my = event.pos
            if 'settings_open' in globals() and settings_open:
                left = slider_rect.left
                width = slider_rect.width
            else:
                left = volume_bar_rect.left
                width = volume_bar_rect.width
            rel = (mx - left) / width
            rel = max(0.0, min(1.0, rel))
            with lock:
                input_gain = rel * 5.0

        # Handle keyboard input
        if event.type == pygame.KEYDOWN:
            # If settings panel is open (paused), pressing any key should close it and resume
            if show_guide:
                show_guide = False
                continue

            if settings_open:
                settings_open = False
            # If on the START or GAME_OVER screen and settings not open, start/restart the game
            elif (game_state == "START" or game_state == "GAME_OVER"):
                # Reset all game variables for a fresh start
                player_vx = velocity_y = 0
                score = 0
                scroll = 0
                is_jumping = False
                # Recreate platforms and hazards
                generate_initial_platforms()
                hazards.clear() # 清空障碍物
                # Snap player to the initial platform (so they don't immediately fall)
                try:
                    init_plat = platforms[0][0]  # platforms store tuples (Rect, ...)
                    player_x = init_plat.left + (init_plat.width - player_w) / 2
                    player_y = init_plat.top - player_h
                except Exception:
                    # Fallback to default positions if platforms list missing
                    player_x = WIDTH//2 - player_w//2
                    player_y = HEIGHT - 200

                # Start grace period so the player doesn't die immediately
                spawn_grace_end = time.time() + SPAWN_GRACE_DURATION
                game_state = "PLAYING"

    # --- State Machine ---
    if game_state == "START":
        screen.fill((20, 24, 30))
        title_font = pygame.font.SysFont(None, 72)
        info_font = pygame.font.SysFont(None, 28)
        title_text = title_font.render("Sound Jumper", True, (220, 220, 255))
        screen.blit(title_text, (WIDTH//2 - title_text.get_width()//2, HEIGHT//6))

        # Draw menu buttons
        for label, rect in MENU_BUTTONS:
            pygame.draw.rect(screen, (40, 40, 60), rect)
            pygame.draw.rect(screen, (120, 120, 160), rect, 2)
            text_surf = FONT.render(label, True, (220, 220, 255))
            screen.blit(text_surf, (rect.left + rect.width//2 - text_surf.get_width()//2, rect.top + rect.height//2 - text_surf.get_height()//2))

        # If the guide screen is active, overlay guide text and a Back button
        if show_guide:
            guide_rect = pygame.Rect(40, 80, WIDTH - 80, HEIGHT - 160)
            pygame.draw.rect(screen, (18, 18, 24), guide_rect)
            pygame.draw.rect(screen, (100, 100, 140), guide_rect, 2)
            guide_text = (
                "How to play:\n"
                "Use A/D or ←/→ to move left/right. Make noise into your mic to jump — louder noise = higher jump.\n"
                "Orange platforms bounce; grey ones break. Avoid red hazards. Score increases as you ascend.\n\n"
                "Click anywhere or press any key to go back."
            )
            # Render wrapped lines within guide_rect with margins
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
        # 横向输入
        keys = pygame.key.get_pressed()
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            player_vx = -speed
        elif keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            player_vx = speed
        else:
            player_vx = 0

        # 获取当前音量 (HUD still updates while paused)
        with lock:
            current_rms = volume_rms

        # If settings panel is open, pause gameplay updates so the player can tune settings.
        paused = settings_open

        # Only run gameplay updates while not paused
        if not paused:
            jump_force = 0.0
            if current_rms > VOLUME_THRESHOLD:
                jump_force = (current_rms - VOLUME_THRESHOLD) * VOLUME_SENSITIVITY
                if jump_force > 18:
                    jump_force = 18

            # --- Physics Update ---
            player_x += player_vx
            player_y += velocity_y
            
            player_rect = pygame.Rect(int(player_x), int(player_y), player_w, player_h)
            
            # --- 平台碰撞和破碎逻辑 ---
            standing_on_platform = None
            is_on_bouncy_platform = False
            
            if velocity_y >= 0:  # 仅在下落时检查
                new_platforms = []
                platform_landed_index = -1
                
                for i, (plat_rect, is_bouncing, is_broken, is_falling) in enumerate(platforms):
                    
                    if not is_falling and player_rect.colliderect(plat_rect) and abs(player_rect.bottom - plat_rect.top) < velocity_y + 1:
                        # 发生在未破碎且未下坠的平台上的碰撞
                        standing_on_platform = plat_rect
                        is_on_bouncy_platform = is_bouncing 
                        platform_landed_index = i # 记录索引

                        # 平台破碎逻辑 (非弹跳平台，且玩家是主动踩上去的)
                        # Do NOT break the initial starting platform (index 0) to avoid immediate death on start
                        if not is_bouncing and i != 0:
                            platforms[i] = (plat_rect, is_bouncing, True, True) # 标记为破碎并开始下坠
                        
                        break
                
                # 如果站在平台上，则修正玩家位置和速度
                if standing_on_platform:
                    player_y = standing_on_platform.top - player_h # Snap to top of platform
                    velocity_y = 0
                    is_jumping = False
                else:
                    # 平台破碎/下坠处理
                    velocity_y += gravity
            else:
                # 应用重力
                velocity_y += gravity

            # Jump Logic (保持不变)
            base_jump_vel = - (6 + jump_force)
            
            if standing_on_platform and jump_force > 1.0 and not is_jumping:
                if is_on_bouncy_platform:
                    velocity_y = base_jump_vel * BOUNCE_MULTIPLIER
                else:
                    velocity_y = base_jump_vel
                    
                is_jumping = True
            
            # 弹跳平台自动弹跳 
            if standing_on_platform and is_on_bouncy_platform and not is_jumping and jump_force < 1.0:
                 velocity_y = - (12) 
                 is_jumping = True


            # --- 障碍物碰撞检测 ---
            for hazard_rect, _ in hazards:
                if player_rect.colliderect(hazard_rect):
                    # Ignore hazard collisions during the spawn grace period
                    if time.time() >= spawn_grace_end:
                        game_state = "GAME_OVER"
                        break # 游戏结束，跳出循环
            
            if game_state == "GAME_OVER":
                # If game ended, skip further update logic and continue to rendering for GAME_OVER
                continue

            # --- 屏幕滚动和平台生成/更新 ---
            if player_y < HEIGHT / 2.5:
                scroll_amount = (HEIGHT / 2.5) - player_y
                player_y += scroll_amount
                scroll += scroll_amount
                
                # 1. 更新所有平台位置和状态
                new_platforms = []
                highest_platform_y = HEIGHT
                
                for plat_rect, is_bouncing, is_broken, is_falling in platforms:
                    
                    # 如果平台正在下坠，让它加速向下移动（独立于屏幕滚动）
                    if is_falling:
                        plat_rect.y += PLATFORM_FALL_SPEED 
                    else:
                        plat_rect.y += scroll_amount # 平台向下滚动

                    # 仅保留仍在屏幕上方或视野内的平台
                    if plat_rect.bottom > 0:
                        new_platforms.append((plat_rect, is_bouncing, is_broken, is_falling))
                        # 追踪当前可见的、未下坠的最高平台Y坐标
                        if not is_falling and plat_rect.y < highest_platform_y:
                            highest_platform_y = plat_rect.y
                
                platforms = new_platforms

                # 2. 更新和生成障碍物
                new_hazards = []
                for hazard_rect, vx in hazards:
                    hazard_rect.y += scroll_amount # 障碍物随屏幕向下滚动
                    
                    # 仅保留在屏幕上方的障碍物
                    if hazard_rect.bottom > 0:
                        new_hazards.append((hazard_rect, vx))
                hazards = new_hazards
                
                # 3. 生成新的平台和障碍物
                if len(platforms) < 10 or highest_platform_y > 0: 
                    y = highest_platform_y
                    
                    # 生成新平台
                    while y > -HEIGHT:
                        y -= random.randint(80, 120)
                        x = random.randint(0, WIDTH - PLATFORM_WIDTH)
                        is_bouncing = random.random() < 0.25
                        platforms.append((pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT), is_bouncing, False, False))
                    
                    # 随机生成新障碍物 (每生成一组平台，随机添加 1-2 个障碍)
                    if random.random() < 0.5:
                        generate_hazard(highest_platform_y)
                    if random.random() < 0.2:
                        generate_hazard(highest_platform_y - 200) # 生成得更高一点


            score = int(scroll / 10)

            # --- 障碍物移动逻辑 ---
            for i, (hazard_rect, vx) in enumerate(hazards):
                hazard_rect.x += vx
                # 碰撞边界反弹
                if hazard_rect.left < 0 or hazard_rect.right > WIDTH:
                    vx = -vx
                    hazards[i] = (hazard_rect, vx) # 更新速度

            # --- Boundaries and Game Over ---
            if player_x < -player_w: player_x = WIDTH
            elif player_x > WIDTH: player_x = -player_w
            
            # Falling below the screen causes game over, but ignore during grace
            if player_y > HEIGHT and time.time() >= spawn_grace_end:
                game_state = "GAME_OVER"

        # --- Rendering (Updated) ---
        screen.fill((20, 24, 30))
        
        # 绘制平台
        for plat_rect, is_bouncing, is_broken, is_falling in platforms:
            if is_falling:
                color = BROKEN_PLATFORM_COLOR
            else:
                color = BOUNCE_PLATFORM_COLOR if is_bouncing else NORMAL_PLATFORM_COLOR
            pygame.draw.rect(screen, color, plat_rect)

        # 绘制障碍物
        for hazard_rect, _ in hazards:
            pygame.draw.circle(screen, HAZARD_COLOR, hazard_rect.center, HAZARD_SIZE // 2)

        pygame.draw.rect(screen, (200, 80, 120), (int(player_x), int(player_y), player_w, player_h))
        
        vol_pct = min(1.0, current_rms / 0.02)
        pygame.draw.rect(screen, (40,40,40), (10, 10, 200, 16))
        pygame.draw.rect(screen, (80, 200, 120), (10, 10, int(200 * vol_pct), 16))
        score_text = FONT.render(f"Score: {score}", True, (200,200,200))
        screen.blit(score_text, (WIDTH - score_text.get_width() - 10, 10))
        screen.blit(FONT.render("A/D or ←/→ to move, make noise to jump. Orange platforms are safe.", True, (200,200,200)), (10, 40))

        # --- Settings icon and gain UI ---
        # Draw settings icon (top-left)
        pygame.draw.rect(screen, (30, 30, 40), settings_icon_rect)
        pygame.draw.circle(screen, (200, 200, 200), settings_icon_rect.center, 12, 2)
        for i in range(6):
            ang = i * (2 * np.pi / 6)
            x = settings_icon_rect.centerx + int(14 * np.cos(ang))
            y = settings_icon_rect.centery + int(14 * np.sin(ang))
            pygame.draw.circle(screen, (200,200,200), (x,y), 2)

        # Draw gain handle on the HUD volume bar
        with lock:
            g_display = input_gain
        handle_x_bar = int(volume_bar_rect.left + (g_display / 5.0) * volume_bar_rect.width)
        handle_y_bar = volume_bar_rect.centery
        pygame.draw.circle(screen, (200,200,160), (handle_x_bar, handle_y_bar), slider_handle_radius)

        # Draw settings panel if open
        if settings_open:
            pygame.draw.rect(screen, (20,20,30), settings_rect)
            pygame.draw.rect(screen, (60,60,70), slider_rect)
            handle_x = int(slider_rect.left + (g_display / 5.0) * slider_rect.width)
            handle_y = slider_rect.centery
            pygame.draw.circle(screen, (180, 220, 200), (handle_x, handle_y), slider_handle_radius)
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

    pygame.display.flip()
    clock.tick(60)

# 退出前停止音频流
audio_stream.stop()
audio_stream.close()
pygame.quit()