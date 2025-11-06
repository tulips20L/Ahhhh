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
    platforms.append(pygame.Rect(WIDTH // 2 - 50, HEIGHT - 100, 100, 12))
    y = HEIGHT - 250
    while y > -HEIGHT:
        x = random.randint(0, WIDTH - PLATFORM_WIDTH)
        platforms.append(pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT))
        y -= random.randint(80, 120)

generate_initial_platforms()

# Game State
score = 0
is_jumping = False
scroll = 0
game_state = "START" # Can be "START", "PLAYING", "GAME_OVER"

# 启动音频线程/流
audio_stream = start_audio_stream()

running = True
while running:
    # --- Event Handling (common to all states) ---
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        # Handle input to start/restart the game
        if (game_state == "START" or game_state == "GAME_OVER") and event.type == pygame.KEYDOWN:
            # Reset all game variables for a fresh start
            player_x = WIDTH//2 - player_w//2
            player_y = HEIGHT - 200
            player_vx = velocity_y = 0
            score = 0
            scroll = 0
            is_jumping = False
            generate_initial_platforms()
            game_state = "PLAYING"

    # --- State Machine ---
    if game_state == "START":
        screen.fill((20, 24, 30))
        title_font = pygame.font.SysFont(None, 72)
        info_font = pygame.font.SysFont(None, 36)
        
        title_text = title_font.render("Sound Jumper", True, (200, 200, 200))
        info_text = info_font.render("Press any key to start", True, (150, 150, 150))
        
        screen.blit(title_text, (WIDTH//2 - title_text.get_width()//2, HEIGHT//3))
        screen.blit(info_text, (WIDTH//2 - info_text.get_width()//2, HEIGHT//2))

    elif game_state == "PLAYING":
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

        # --- Physics Update ---
        player_x += player_vx
        player_y += velocity_y
        
        player_rect = pygame.Rect(int(player_x), int(player_y), player_w, player_h)
        
        # --- Collision Detection (Corrected) ---
        standing_on = None
        if velocity_y >= 0:  # Only check for landing when falling
            for plat in platforms:
                # Check if the player is colliding AND their bottom is near the platform's top
                if player_rect.colliderect(plat) and abs(player_rect.bottom - plat.top) < velocity_y + 1:
                    standing_on = plat
                    break
        
        if standing_on:
            player_y = standing_on.top - player_h # Snap to top of platform
            velocity_y = 0
            is_jumping = False
        else:
            # Apply gravity if not on a platform
            velocity_y += gravity

        # Jump Logic
        if standing_on and jump_force > 1.0 and not is_jumping:
            velocity_y = - (6 + jump_force)
            is_jumping = True

        # --- Screen Scrolling ---
        if player_y < HEIGHT / 2.5:
            scroll_amount = (HEIGHT / 2.5) - player_y
            player_y += scroll_amount
            scroll += scroll_amount
            for plat in platforms:
                plat.y += scroll_amount
            
            platforms = [p for p in platforms if p.bottom > 0 and p.top < HEIGHT]

            highest_platform_y = min(p.y for p in platforms) if platforms else HEIGHT
            if len(platforms) < 15:
                y = highest_platform_y
                while y > -HEIGHT:
                    y -= random.randint(80, 120)
                    x = random.randint(0, WIDTH - PLATFORM_WIDTH)
                    platforms.append(pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT))
        
        score = int(scroll / 10)

        # --- Boundaries ---
        if player_x < -player_w: player_x = WIDTH
        elif player_x > WIDTH: player_x = -player_w
        
        if player_y > HEIGHT:
            game_state = "GAME_OVER"

        # --- Rendering ---
        screen.fill((20, 24, 30))
        for plat in platforms:
            pygame.draw.rect(screen, (180, 180, 100), plat)
        pygame.draw.rect(screen, (200, 80, 120), (int(player_x), int(player_y), player_w, player_h))
        vol_pct = min(1.0, current_rms / 0.02)
        pygame.draw.rect(screen, (40,40,40), (10, 10, 200, 16))
        pygame.draw.rect(screen, (80, 200, 120), (10, 10, int(200 * vol_pct), 16))
        score_text = FONT.render(f"Score: {score}", True, (200,200,200))
        screen.blit(score_text, (WIDTH - score_text.get_width() - 10, 10))
        screen.blit(FONT.render("A/D or ←/→ to move, make noise to jump.", True, (200,200,200)), (10, 40))

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
