import pygame
import numpy as np
import sounddevice as sd
import threading
import time
import random
from scipy.interpolate import interp1d

# ---------- 音频采样设置 ----------
SAMPLE_RATE = 44100
FRAME_SIZE = 1024 # Must be a power of 2 for FFT
volume_rms = 0.0
# New: For FFT frequency data
fft_data = np.zeros(FRAME_SIZE // 2)
lock = threading.Lock()

def audio_callback(indata, frames, time_info, status):
    global volume_rms, fft_data
    if status:
        pass
    mono = np.mean(indata, axis=1) if indata.ndim > 1 else indata
    rms = np.sqrt(np.mean(mono.astype(np.float64)**2))
    
    # Perform FFT
    # Apply a window function to reduce spectral leakage
    window = np.hanning(len(mono))
    mono_windowed = mono * window
    # Get the magnitude of the FFT
    fft_result = np.fft.rfft(mono_windowed)
    fft_magnitude = np.abs(fft_result)

    with lock:
        volume_rms = rms
        fft_data = fft_magnitude.copy()

def start_audio_stream():
    stream = sd.InputStream(channels=1, samplerate=SAMPLE_RATE,
                            blocksize=FRAME_SIZE, callback=audio_callback)
    stream.start()
    return stream

# ---------- Pygame 游戏设置 ----------
pygame.init()
WIDTH, HEIGHT = 480, 720
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Sound Jumper - Equalizer")

clock = pygame.time.Clock()
FONT = pygame.font.SysFont(None, 24)

# 角色
player_w, player_h = 40, 40
player_x, player_y = WIDTH//2 - player_w//2, HEIGHT - 200
player_vx, velocity_y, speed = 0, 0, 5

# 物理
gravity = 0.6

# 音量 → 跳跃力 映射参数
VOLUME_THRESHOLD = 0.003
VOLUME_SENSITIVITY = 2000

# 平台
platforms = []
PLATFORM_WIDTH, PLATFORM_HEIGHT = 100, 12

def generate_initial_platforms():
    global platforms
    platforms.clear()
    platforms.append(pygame.Rect(WIDTH // 2 - 50, HEIGHT - 100, 100, 12))
    y = HEIGHT - 250
    while y > -HEIGHT:
        x, y = random.randint(0, WIDTH - PLATFORM_WIDTH), y - random.randint(80, 120)
        platforms.append(pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT))

# --- NEW: Equalizer Background Setup ---
BG_COLOR = (5, 0, 15)
NUM_BARS = 32
BAR_WIDTH = WIDTH / NUM_BARS
SEGMENT_HEIGHT = 6

# Color gradient for the bars (Pink -> Purple -> Blue -> Cyan)
GRADIENT = [
    (255, 0, 150), (200, 0, 200), (100, 0, 255),
    (0, 100, 255), (0, 200, 255), (0, 255, 200)
]

# Helper to get a smooth color from the gradient
def get_gradient_color(value): # value is 0 to 1
    value = max(0, min(1, value)) * (len(GRADIENT) - 1)
    idx1, idx2 = int(value), min(int(value) + 1, len(GRADIENT) - 1)
    interp = value - idx1
    c1, c2 = GRADIENT[idx1], GRADIENT[idx2]
    return tuple(int(c1[i] + (c2[i] - c1[i]) * interp) for i in range(3))

class EqualizerBar:
    def __init__(self, x):
        self.x = x
        self.height = 0
        self.target_height = 0
        self.smoothing_factor = 0.3 # Controls how fast the bar moves

    def update(self, target):
        self.target_height = target
        # Smoothly animate towards the target height
        self.height += (self.target_height - self.height) * self.smoothing_factor
    
    def draw(self, surface):
        num_segments = int(self.height / SEGMENT_HEIGHT)
        for i in range(num_segments):
            y_pos = HEIGHT - (i + 1) * SEGMENT_HEIGHT
            # Color is based on vertical position
            color_value = (y_pos / HEIGHT) * 0.6 + 0.4 # Map color to top 60% of screen
            color = get_gradient_color(1 - color_value)
            
            rect = pygame.Rect(self.x, y_pos, BAR_WIDTH - 2, SEGMENT_HEIGHT - 2)
            pygame.draw.rect(surface, color, rect)

# Create the equalizer bars
equalizer_bars = [EqualizerBar(i * BAR_WIDTH) for i in range(NUM_BARS)]

# --- Game State ---
score, is_jumping, scroll, game_state = 0, False, 0, "START"

# 启动音频线程/流
audio_stream = start_audio_stream()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if (game_state == "START" or game_state == "GAME_OVER") and event.type == pygame.KEYDOWN:
            player_x, player_y = WIDTH//2 - player_w//2, HEIGHT - 200
            player_vx, velocity_y, score, scroll, is_jumping = 0, 0, 0, 0, False
            generate_initial_platforms()
            game_state = "PLAYING"

    with lock:
        current_rms = volume_rms
        current_fft = fft_data.copy()

    # --- Update Equalizer Bars ---
    # Map FFT data to the bars. We use logarithmic scaling for better visual distribution.
    log_indices = np.logspace(0, np.log10(len(current_fft) - 1), NUM_BARS).astype(int)
    for i, bar in enumerate(equalizer_bars):
        start = log_indices[i-1] if i > 0 else 0
        end = log_indices[i]
        # Get the max frequency magnitude in this bar's "bin"
        fft_bin_val = np.max(current_fft[start:end]) if end > start else current_fft[start]
        # Scale the height. This requires tweaking for good visuals.
        target_h = min(HEIGHT, (fft_bin_val**0.6) * 20)
        bar.update(target_h)

    # --- Game Logic ---
    if game_state == "PLAYING":
        keys = pygame.key.get_pressed()
        player_vx = -speed if keys[pygame.K_a] or keys[pygame.K_LEFT] else speed if keys[pygame.K_d] or keys[pygame.K_RIGHT] else 0
        jump_force = max(0, min(18, (current_rms - VOLUME_THRESHOLD) * VOLUME_SENSITIVITY))

        player_x += player_vx
        player_y += velocity_y
        player_rect = pygame.Rect(int(player_x), int(player_y), player_w, player_h)
        
        standing_on = None
        if velocity_y >= 0:
            for plat in platforms:
                if player_rect.colliderect(plat) and abs(player_rect.bottom - plat.top) < velocity_y + 1:
                    standing_on = plat
                    break
        
        if standing_on:
            player_y = standing_on.top - player_h
            velocity_y = 0
            is_jumping = False
        else:
            velocity_y += gravity

        if standing_on and jump_force > 1.0 and not is_jumping:
            velocity_y = - (6 + jump_force)
            is_jumping = True

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
                    x, y = random.randint(0, WIDTH - PLATFORM_WIDTH), y - random.randint(80, 120)
                    platforms.append(pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT))
        score = int(scroll / 10)
        if player_x < -player_w: player_x = WIDTH
        elif player_x > WIDTH: player_x = -player_w
        if player_y > HEIGHT: game_state = "GAME_OVER"

    # --- Rendering ---
    screen.fill(BG_COLOR)
    
    # 1. Draw Equalizer Background
    for bar in equalizer_bars:
        bar.draw(screen)

    # 2. Draw Game Elements
    if game_state == "START":
        title_font, info_font = pygame.font.SysFont(None, 72), pygame.font.SysFont(None, 36)
        title_text, info_text = title_font.render("Sound Jumper", True, (220, 220, 255)), info_font.render("Press any key to start", True, (180, 180, 220))
        screen.blit(title_text, (WIDTH//2 - title_text.get_width()//2, HEIGHT//3))
        screen.blit(info_text, (WIDTH//2 - info_text.get_width()//2, HEIGHT//2))
    elif game_state == "PLAYING":
        for plat in platforms: pygame.draw.rect(screen, (255, 220, 0), plat)
        pygame.draw.rect(screen, (255, 100, 180), (int(player_x), int(player_y), player_w, player_h))
        vol_pct = min(1.0, current_rms / 0.02)
        pygame.draw.rect(screen, (40,40,40), (10, 10, 200, 16))
        pygame.draw.rect(screen, (100, 255, 180), (10, 10, int(200 * vol_pct), 16))
        score_text = FONT.render(f"Score: {score}", True, (220, 220, 255))
        screen.blit(score_text, (WIDTH - score_text.get_width() - 10, 10))
        screen.blit(FONT.render("A/D or ←/→ to move, make noise to jump.", True, (220, 220, 255)), (10, 40))
    elif game_state == "GAME_OVER":
        title_font, score_font, info_font = pygame.font.SysFont(None, 72), pygame.font.SysFont(None, 48), pygame.font.SysFont(None, 36)
        title_text, score_text_val, info_text = title_font.render("Game Over", True, (255, 100, 180)), score_font.render(f"Final Score: {score}", True, (220, 220, 255)), info_font.render("Press any key to play again", True, (180, 180, 220))
        screen.blit(title_text, (WIDTH//2 - title_text.get_width()//2, HEIGHT//4))
        screen.blit(score_text_val, (WIDTH//2 - score_text_val.get_width()//2, HEIGHT//2 - 50))
        screen.blit(info_text, (WIDTH//2 - info_text.get_width()//2, HEIGHT//2 + 20))

    pygame.display.flip()
    clock.tick(60)

audio_stream.stop()
audio_stream.close()
pygame.quit()
