import pygame
import numpy as np
import sounddevice as sd
import threading
import time

# ---------- 音频采样设置 ----------
SAMPLE_RATE = 44100
FRAME_SIZE = 1024  # 帧大小，越小延迟越低但波动更大
volume_rms = 0.0   # 共享变量，保存当前音量RMS
lock = threading.Lock()

def audio_callback(indata, frames, time_info, status):
    """sounddevice 回调：更新全局 volume_rms"""
    global volume_rms
    if status:
        # print(status)
        pass
    # indata.shape = (frames, channels)
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
pygame.display.set_caption("Sound Jumper - Prototype")

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
GROUND_Y = HEIGHT - 40

# 音量 → 跳跃力 映射参数（可在游戏内调整）
VOLUME_THRESHOLD = 0.003  # 低于这个基本不触发
VOLUME_SENSITIVITY = 2000  # 增强映射（越大反应越强）

# 平台（简单示例）
platforms = [
    pygame.Rect(WIDTH//2 - 50, HEIGHT - 100, 100, 12),  # 初始平台
    pygame.Rect(50, HEIGHT - 250, 100, 12),
    pygame.Rect(300, HEIGHT - 400, 100, 12),
]

score = 0
is_jumping = False

# 启动音频线程/流
audio_stream = start_audio_stream()

running = True
while running:
    dt = clock.tick(60) / 1000.0  # 秒
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

    # 获取当前音量（线程安全）
    with lock:
        current_rms = volume_rms

    # 将 RMS 映射为跳跃速度（仅在接触平台或地面时触发跳跃）
    # 公式示例： jump_force = (current_rms - threshold) * sensitivity
    jump_force = 0.0
    if current_rms > VOLUME_THRESHOLD:
        jump_force = (current_rms - VOLUME_THRESHOLD) * VOLUME_SENSITIVITY
        # 限制最大跳跃力
        if jump_force > 18:
            jump_force = 18

    # 允许在碰到平台或地面时被声音触发跳跃
    player_rect = pygame.Rect(int(player_x), int(player_y), player_w, player_h)

    # 简单碰撞检测：是否在某个平台正上方并且接触（用于判断是否可以被声音触发跳跃）
    standing_on = None
    for plat in platforms:
        if (player_rect.bottom >= plat.top - 5 and player_rect.bottom <= plat.top + 10 and
            player_rect.right > plat.left + 5 and player_rect.left < plat.right - 5 and velocity_y >= 0):
            standing_on = plat
            break

    on_ground = player_rect.bottom >= GROUND_Y

    # 如果站在平台或地面，修正位置并重置跳跃状态
    if standing_on:
        player_y = standing_on.top - player_h
        velocity_y = 0
        is_jumping = False
    elif on_ground:
        if player_rect.bottom > GROUND_Y:
            player_y = GROUND_Y - player_h
        velocity_y = 0
        is_jumping = False

    # 声音触发跳跃（仅当在地面/平台上并且当前不处于跳跃中）
    if (standing_on or on_ground) and jump_force > 1.0 and not is_jumping:
        velocity_y = - (6 + jump_force)  # 基础跳跃力 + 音量增强
        is_jumping = True
    else:
        # 在空中受重力影响
        if not (standing_on or on_ground):
            velocity_y += gravity

    # 更新位置
    player_x += player_vx
    player_y += velocity_y

    # 横向边界处理（屏幕循环）
    if player_x < -player_w:
        player_x = WIDTH
    elif player_x > WIDTH:
        player_x = -player_w

    # 简单死亡判断：掉出底部
    if player_y > HEIGHT + 200:
        # 重置（在原地复活）
        player_x = WIDTH//2 - player_w//2
        player_y = HEIGHT - 200
        player_vx = player_vy = 0
        score = 0

    # 渲染
    screen.fill((20, 24, 30))
    # 平台
    for plat in platforms:
        pygame.draw.rect(screen, (180, 180, 100), plat)
    # 地面线
    pygame.draw.rect(screen, (60, 60, 60), (0, GROUND_Y, WIDTH, HEIGHT - GROUND_Y))
    # player
    pygame.draw.rect(screen, (200, 80, 120), (int(player_x), int(player_y), player_w, player_h))

    # 显示音量条
    vol_pct = min(1.0, current_rms / 0.02)  # 归一化（方便观察）
    pygame.draw.rect(screen, (40,40,40), (10, 10, 200, 16))
    pygame.draw.rect(screen, (80, 200, 120), (10, 10, int(200 * vol_pct), 16))
    screen.blit(FONT.render(f"RMS: {current_rms:.6f}", True, (200,200,200)), (220, 8))
    screen.blit(FONT.render(f"JumpForce: {jump_force:.2f}", True, (200,200,200)), (220, 30))
    screen.blit(FONT.render("Use A/D or ←/→ to move. Make noise to jump.", True, (200,200,200)), (10, 40))
    pygame.display.flip()

# 退出前停止音频流
audio_stream.stop()
audio_stream.close()
pygame.quit()
