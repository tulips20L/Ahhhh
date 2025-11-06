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
lock = threading.Lock()

def audio_callback(indata, frames, time_info, status):
    global volume_rms
    if status:
        pass
    mono = np.mean(indata, axis=1) if indata.ndim > 1 else indata
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

# 音量 → 跳跃力 映射参数
VOLUME_THRESHOLD = 0.003
VOLUME_SENSITIVITY = 2000

# 平台
platforms = []
PLATFORM_WIDTH, PLATFORM_HEIGHT = 100, 12

def generate_initial_platforms():
    """Generates the starting platforms and resets the list."""
    global platforms
    platforms.clear()
    # Start platform
    platforms.append(pygame.Rect(WIDTH // 2 - 50, HEIGHT - 100, 100, 12))
    # Generate some platforms above the start
    y = HEIGHT - 250
    while y > -HEIGHT: # Generate up to one screen height above the start
        x = random.randint(0, WIDTH - PLATFORM_WIDTH)
        platforms.append(pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT))
        y -= random.randint(80, 120)

generate_initial_platforms() # Create the first set of platforms

score = 0
is_jumping = False
scroll = 0 # New variable to track scrolling

# 启动音频线程/流
audio_stream = start_audio_stream()

running = True
while running:
    clock.tick(60)
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    # 横向输入
    keys = pygame.key.get_pressed()
    if keys[pygame.K_a] or keys[pygame.K_LEFT]:
        player_vx = -speed
    elif keys[pygame.K_d] or keys[pygame.K_RIGHT]:
        player_vx = speed
    else:
        player_vx = 0

    # 获取当前音量
    with lock:
        current_rms = volume_rms

    jump_force = 0.0
    if current_rms > VOLUME_THRESHOLD:
        jump_force = (current_rms - VOLUME_THRESHOLD) * VOLUME_SENSITIVITY
        if jump_force > 18:
            jump_force = 18

    player_rect = pygame.Rect(int(player_x), int(player_y), player_w, player_h)
    
    # Check for landing on a platform
    standing_on = None
    if velocity_y >= 0: # Only check for landing if falling down
        for plat in platforms:
            # Check collision and that the player is above the platform
            if player_rect.colliderect(plat) and player_rect.bottom < plat.bottom:
                standing_on = plat
                break

    # If standing on a platform, correct position and reset jump state
    if standing_on:
        player_y = standing_on.top - player_h
        velocity_y = 0
        is_jumping = False

    # Sound-triggered jump
    if standing_on and jump_force > 1.0 and not is_jumping:
        velocity_y = - (6 + jump_force)
        is_jumping = True
    else:
        # Apply gravity if in the air
        if not standing_on:
            velocity_y += gravity

    # 更新位置
    player_x += player_vx
    player_y += velocity_y

    # --- Screen Scrolling & Map Generation ---
    if player_y < HEIGHT / 2.5: # If player is in the top 40% of the screen
        scroll_amount = (HEIGHT / 2.5) - player_y
        player_y += scroll_amount  # Move player down to the threshold
        scroll += scroll_amount    # Increase total scroll
        
        # Move all platforms down
        for plat in platforms:
            plat.y += scroll_amount

        # Remove platforms that have scrolled off the bottom
        platforms = [p for p in platforms if p.top < HEIGHT]

        # Generate new platforms at the top
        highest_platform_y = min(p.y for p in platforms) if platforms else HEIGHT
        if len(platforms) < 15: # Maintain a certain number of platforms
            y = highest_platform_y
            while y > -HEIGHT: # Generate new platforms up to one screen height above
                y -= random.randint(80, 120)
                x = random.randint(0, WIDTH - PLATFORM_WIDTH)
                platforms.append(pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT))
    
    score = int(scroll / 10) # Update score based on scroll amount

    # 横向边界处理
    if player_x < -player_w:
        player_x = WIDTH
    elif player_x > WIDTH:
        player_x = -player_w

    # Game Over:掉出底部
    if player_y > HEIGHT:
        # Reset game state
        player_x = WIDTH//2 - player_w//2
        player_y = HEIGHT - 200
        player_vx = velocity_y = 0
        score = 0
        scroll = 0
        generate_initial_platforms() # Regenerate the map from the start

    # 渲染
    screen.fill((20, 24, 30))
    for plat in platforms:
        pygame.draw.rect(screen, (180, 180, 100), plat)
    pygame.draw.rect(screen, (200, 80, 120), (int(player_x), int(player_y), player_w, player_h))

    # UI
    vol_pct = min(1.0, current_rms / 0.02)
    pygame.draw.rect(screen, (40,40,40), (10, 10, 200, 16))
    pygame.draw.rect(screen, (80, 200, 120), (10, 10, int(200 * vol_pct), 16))
    score_text = FONT.render(f"Score: {score}", True, (200,200,200))
    screen.blit(score_text, (WIDTH - score_text.get_width() - 10, 10))
    # --- EDITED LINE ---
    screen.blit(FONT.render("A/D or ←/→ to move, make noise to jump.", True, (200,200,200)), (10, 40))
    pygame.display.flip()

# 退出前停止音频流
audio_stream.stop()
audio_stream.close()
pygame.quit()
