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
# Input gain (multiplier applied to incoming audio)
input_gain = 1.0
# New: For FFT frequency data
fft_data = np.zeros(FRAME_SIZE // 2)
lock = threading.Lock()

def audio_callback(indata, frames, time_info, status):
    global volume_rms, fft_data
    if status:
        pass
    # Convert to mono
    mono = np.mean(indata, axis=1) if indata.ndim > 1 else indata
    # Read current input gain (thread-safe)
    with lock:
        g = input_gain
    mono = mono * g
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


# In-game settings UI will be rendered with pygame (no external GUI)

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

# Pygame in-game settings UI state
settings_open = False
# UI layout: settings icon top-left, volume bar below, instructions under that
settings_icon_rect = pygame.Rect(10, 10, 32, 32)
volume_bar_rect = pygame.Rect(10, 50, 200, 12)  # RMS volume bar
gain_label_pos = (volume_bar_rect.right + 10, volume_bar_rect.top - 4)
instructions_pos = (10, 74)
# Settings panel opens on the right near the top (avoids overlapping HUD)
settings_rect = pygame.Rect(WIDTH - 320, 10, 300, 80)
slider_rect = pygame.Rect(settings_rect.left + 16, settings_rect.top + 36, settings_rect.width - 32, 10)
slider_handle_radius = 8
dragging_slider = False

# Start audio stream
audio_stream = start_audio_stream()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos
            # Click anywhere to start the game from START or GAME_OVER
            if game_state in ("START", "GAME_OVER"):
                player_x, player_y = WIDTH//2 - player_w//2, HEIGHT - 200
                player_vx, velocity_y, score, scroll, is_jumping = 0, 0, 0, 0, False
                generate_initial_platforms()
                game_state = "PLAYING"

            if settings_icon_rect.collidepoint(mx, my):
                settings_open = not settings_open
            # Start dragging the slider when clicking on the settings slider (if open)
            if settings_open and slider_rect.collidepoint(mx, my):
                dragging_slider = True
            # Also allow clicking/drags directly on the RMS volume bar to change gain anytime
            if volume_bar_rect.collidepoint(mx, my):
                # Map x to gain immediately
                rel = (mx - volume_bar_rect.left) / volume_bar_rect.width
                rel = max(0.0, min(1.0, rel))
                with lock:
                    input_gain = rel * 5.0
                dragging_slider = True

        if event.type == pygame.MOUSEBUTTONUP:
            dragging_slider = False

        if event.type == pygame.MOUSEMOTION and dragging_slider:
            mx, my = event.pos
            # Prefer the settings slider if open, otherwise the volume bar
            if settings_open:
                left = slider_rect.left
                width = slider_rect.width
            else:
                left = volume_bar_rect.left
                width = volume_bar_rect.width
            rel = (mx - left) / width
            rel = max(0.0, min(1.0, rel))
            with lock:
                input_gain = rel * 5.0

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

    # Draw settings icon
    pygame.draw.rect(screen, (30, 30, 40), settings_icon_rect)
    pygame.draw.circle(screen, (200, 200, 200), settings_icon_rect.center, 12, 2)
    # small gear teeth representation
    for i in range(6):
        ang = i * (2 * np.pi / 6)
        x = settings_icon_rect.centerx + int(14 * np.cos(ang))
        y = settings_icon_rect.centery + int(14 * np.sin(ang))
        pygame.draw.circle(screen, (200,200,200), (x,y), 2)

    # 2. Draw Game Elements
    if game_state == "START":
        title_font, info_font = pygame.font.SysFont(None, 72), pygame.font.SysFont(None, 36)
        title_text, info_text = title_font.render("Sound Jumper", True, (220, 220, 255)), info_font.render("Press any key to start", True, (180, 180, 220))
        screen.blit(title_text, (WIDTH//2 - title_text.get_width()//2, HEIGHT//3))
        screen.blit(info_text, (WIDTH//2 - info_text.get_width()//2, HEIGHT//2))
    elif game_state == "PLAYING":
        for plat in platforms:
            pygame.draw.rect(screen, (255, 220, 0), plat)
        pygame.draw.rect(screen, (255, 100, 180), (int(player_x), int(player_y), player_w, player_h))
        vol_pct = min(1.0, current_rms / 0.02)

        # Draw RMS volume bar (moved down to avoid overlap)
        pygame.draw.rect(screen, (40,40,40), volume_bar_rect)
        pygame.draw.rect(screen, (100, 255, 180), (volume_bar_rect.left, volume_bar_rect.top, int(volume_bar_rect.width * vol_pct), volume_bar_rect.height))

        # Display current input gain and draw handle on RMS bar to reflect input_gain
        with lock:
            g_display = input_gain
        screen.blit(FONT.render(f"Gain: {g_display:.2f}x", True, (220,220,255)), gain_label_pos)
        handle_x_bar = int(volume_bar_rect.left + (g_display / 5.0) * volume_bar_rect.width)
        handle_y_bar = volume_bar_rect.centery
        pygame.draw.circle(screen, (200,200,160), (handle_x_bar, handle_y_bar), slider_handle_radius)

        # If settings open, draw slider UI
        if settings_open:
            pygame.draw.rect(screen, (20,20,30), settings_rect)
            # Slider background inside settings panel
            pygame.draw.rect(screen, (60,60,70), slider_rect)
            # Handle position based on gain (0..5)
            handle_x = int(slider_rect.left + (g_display / 5.0) * slider_rect.width)
            handle_y = slider_rect.centery
            pygame.draw.circle(screen, (180, 220, 200), (handle_x, handle_y), slider_handle_radius)
            # Label
            screen.blit(FONT.render(f"Input Gain: {g_display:.2f}x", True, (220,220,255)), (slider_rect.left, slider_rect.top - 22))

        score_text = FONT.render(f"Score: {score}", True, (220, 220, 255))
        screen.blit(score_text, (WIDTH - score_text.get_width() - 10, 10))
        # Instructions moved below the volume bar; click anywhere to start or click/drag the volume bar to adjust gain
        screen.blit(FONT.render("A/D or ←/→ to move, make noise to jump. Click to start. Drag bar to edit gain.", True, (220, 220, 255)), instructions_pos)
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
