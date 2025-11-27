import pygame
import numpy as np
import sounddevice as sd
import threading
import time
import random
import cv2
import mediapipe as mp
import os

# ---------- 1. 初始化 & 屏幕设置 ----------
pygame.init()
pygame.mixer.init()

info = pygame.display.Info()
WIDTH, HEIGHT = info.current_w, info.current_h
screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.FULLSCREEN)
pygame.display.set_caption("Sound Jumper - Final")

# ---------- 2. 音频处理 ----------
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
    try:
        stream = sd.InputStream(channels=1, samplerate=SAMPLE_RATE,
                                blocksize=FRAME_SIZE, callback=audio_callback)
        stream.start()
        return stream
    except Exception as e:
        print(f"音频错误: {e}")
        return None

# ---------- 3. MediaPipe 手势识别 ----------
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

CAMERA_INDEX = 0
cap = None
camera_available = False
try:
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if cap.isOpened():
        ret, frame = cap.read()
        if ret:
            camera_available = True
            print("摄像头已启动")
        else: cap.release(); cap = None
except: pass

def count_extended_fingers(hand_landmarks):
    TIPS = [4, 8, 12, 16, 20]
    PIPS = [2, 6, 10, 14, 18]
    extended = [0, 0, 0, 0, 0]
    for i in range(1, 5):
        if hand_landmarks.landmark[TIPS[i]].y < hand_landmarks.landmark[PIPS[i]].y:
            extended[i] = 1
    if hand_landmarks.landmark[TIPS[0]].x < hand_landmarks.landmark[PIPS[0]].x:
        extended[0] = 1
    count = sum(extended)
    if count >= 4: return "PALM"
    if count <= 1: return "FIST"
    if extended[1] and extended[2] and not extended[0] and not extended[3] and not extended[4]:
        return "VICTORY"
    return "UNKNOWN"

# ---------- 4. 游戏变量 ----------
clock = pygame.time.Clock()
FONT = pygame.font.SysFont(None, 30)
BIG_FONT = pygame.font.SysFont(None, 60)

player_w, player_h = 40, 40
player_x = WIDTH//2 - player_w//2
player_y = -50
velocity_y = 0
gravity = 2
PLATFORM_FALL_SPEED = 20

# ========== [48x48 规格自动切割] ==========
sprite_loaded = False
animation_frames = []
current_frame_index = 0

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    image_path = os.path.join(script_dir, "character_sheet.png")

    if not os.path.exists(image_path):
        print(f"提示: 未找到 {image_path}，将使用默认方块。")
    else:
        sprite_sheet = pygame.image.load(image_path).convert_alpha()
        sheet_width = sprite_sheet.get_width()
        FRAME_W = 48
        FRAME_H = 48
        FRAME_COUNT = sheet_width // FRAME_W
        print(f"加载成功：{sheet_width}x{sprite_sheet.get_height()}，包含 {FRAME_COUNT} 帧")

        animation_frames = []
        for i in range(FRAME_COUNT):
            frame_surf = sprite_sheet.subsurface((i * FRAME_W, 0, FRAME_W, FRAME_H))
            scaled_frame = pygame.transform.scale(frame_surf, (48, 48))
            animation_frames.append(scaled_frame)

        if len(animation_frames) > 0:
            sprite_loaded = True
except Exception as e:
    print(f"加载出错: {e}")
    sprite_loaded = False

# 声音与技能参数
VOLUME_THRESHOLD = 0.001
VOLUME_SENSITIVITY = 4000
BOUNCE_MULTIPLIER = 2.0
volume_sensitivity_adjusted = VOLUME_SENSITIVITY

skills = {
    "RESCUE": {"cooldown": 5.0, "last_use": 0, "color": (255, 165, 0), "name": "Rescue (V-Sign/1)"},
    "SHIELD": {"cooldown": 8.0, "last_use": 0, "color": (255, 215, 0), "name": "Shield (Fist/2)"},
    "BLAST":  {"cooldown": 10.0, "last_use": 0, "color": (0, 255, 255), "name": "Blast (Palm/3)"}
}
shield_active_end = 0.0
shockwave_radius = 0

keyboard_target_x = WIDTH // 2
keyboard_move_speed = 15

platforms = []
hazards = []
PLATFORM_WIDTH, PLATFORM_HEIGHT = 120, 15
HAZARD_SIZE, HAZARD_SPEED = 15, 10

def generate_initial_platforms():
    global platforms
    platforms.clear()
    start_plat_w = 200
    # 【需求实现 1：橙色弹跳平台】
    # 第二个参数设置为 True (is_bouncing)，这会让平台在绘制时变成橙色，并具有弹跳属性
    platforms.append((pygame.Rect(WIDTH // 2 - start_plat_w // 2, HEIGHT - 150, start_plat_w, 15), True, False, False))

    y = HEIGHT - 300
    while y > -HEIGHT:
        x = random.randint(0, WIDTH - PLATFORM_WIDTH)
        is_bouncing = random.random() < 0.25
        platforms.append((pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT), is_bouncing, False, False))
        y -= random.randint(80, 140)

def generate_hazard(highest_y):
    x = random.randint(0, WIDTH)
    y = highest_y - random.randint(100, 300)
    vx = random.choice([-HAZARD_SPEED, HAZARD_SPEED])
    hazards.append((pygame.Rect(x, y, HAZARD_SIZE, HAZARD_SIZE), vx))

generate_initial_platforms()

score = 0
is_jumping = False
scroll = 0
game_state = "START"
hand_target_x = WIDTH // 2
initial_drop = True # 核心标记：是否处于开局下落状态

dim_surface = pygame.Surface((WIDTH, HEIGHT))
dim_surface.set_alpha(160)
dim_surface.fill((0, 0, 0))

audio_stream = start_audio_stream()

running = True
while running:
    # ------------------ 输入 ------------------
    bg_surface = None
    current_gesture = "NONE"

    # 1. 获取摄像头输入
    if camera_available and cap is not None:
        success, image = cap.read()
        if success:
            image = cv2.flip(image, 1)
            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            results = hands.process(image_rgb)
            h, w, c = image.shape

            if results.multi_hand_landmarks:
                for idx, hand_landmarks in enumerate(results.multi_hand_landmarks):
                    label = results.multi_handedness[idx].classification[0].label
                    hand_cx = hand_landmarks.landmark[9].x

                    if label == "Left":
                        target_raw = hand_cx * WIDTH
                        # 更新手部目标，但在 initial_drop 时会被覆盖
                        hand_target_x = max(0, min(WIDTH - player_w, target_raw - player_w/2))
                        cv2.circle(image, (int(hand_cx*w), int(hand_landmarks.landmark[9].y*h)), 15, (0, 255, 0), -1)
                    elif label == "Right":
                        gesture = count_extended_fingers(hand_landmarks)
                        current_gesture = gesture
                        cv2.putText(image, gesture, (int(hand_cx*w)-40, int(hand_landmarks.landmark[9].y*h)-40),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 3)
            bg_image = cv2.resize(image, (WIDTH, HEIGHT))
            bg_surface = pygame.image.frombuffer(bg_image.tobytes(), bg_image.shape[1::-1], "RGB")

    # 2. 获取键盘输入
    keys = pygame.key.get_pressed()
    if not camera_available:
        if keys[pygame.K_LEFT] or keys[pygame.K_a]: keyboard_target_x = max(0, keyboard_target_x - keyboard_move_speed)
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]: keyboard_target_x = min(WIDTH - player_w, keyboard_target_x + keyboard_move_speed)

    # 3. 【需求实现 2：强制锁定逻辑】
    # 这一步必须放在所有输入获取之后，确保覆盖掉任何移动指令
    if initial_drop:
        hand_target_x = WIDTH // 2 - player_w // 2
        keyboard_target_x = WIDTH // 2 - player_w // 2

    if not camera_available:
        hand_target_x = keyboard_target_x

    # ------------------ 事件处理 ------------------
    for event in pygame.event.get():
        if event.type == pygame.QUIT: running = False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE: running = False

            if game_state == "PLAYING" and not camera_available:
                now = time.time()
                if event.key == pygame.K_1 and now - skills["RESCUE"]["last_use"] > skills["RESCUE"]["cooldown"]:
                    spawn_y = min(HEIGHT-50, player_y + 100)
                    platforms.append((pygame.Rect(player_x - 30, spawn_y, PLATFORM_WIDTH, PLATFORM_HEIGHT), True, False, False))
                    skills["RESCUE"]["last_use"] = now
                elif event.key == pygame.K_2 and now - skills["SHIELD"]["last_use"] > skills["SHIELD"]["cooldown"]:
                    shield_active_end = now + 3.0
                    skills["SHIELD"]["last_use"] = now
                elif event.key == pygame.K_3 and now - skills["BLAST"]["last_use"] > skills["BLAST"]["cooldown"]:
                    hazards.clear()
                    shockwave_radius = 1
                    skills["BLAST"]["last_use"] = now

            if game_state == "SETTINGS":
                if event.key == pygame.K_RETURN or event.key == pygame.K_SPACE:
                    velocity_y = 0
                    score = scroll = 0
                    is_jumping = False
                    generate_initial_platforms()
                    hazards.clear()
                    player_x = WIDTH // 2 - player_w // 2
                    player_y = -50
                    keyboard_target_x = WIDTH // 2 - player_w // 2
                    
                    # 重新开始时，重置锁定状态
                    initial_drop = True 
                    
                    for skill in skills.values(): skill['last_use'] = 0
                    game_state = "PLAYING"
            elif game_state == "START": game_state = "SETTINGS"
            elif game_state == "GAME_OVER": game_state = "SETTINGS"

    # 设置界面调节逻辑
    if game_state == "SETTINGS":
        adjustment_speed = 25
        if keys[pygame.K_LEFT]:
            volume_sensitivity_adjusted = max(500, volume_sensitivity_adjusted - adjustment_speed)
        if keys[pygame.K_RIGHT]:
            volume_sensitivity_adjusted = min(8000, volume_sensitivity_adjusted + adjustment_speed)
            
    now = time.time()
    if game_state == "PLAYING" and camera_available:
        # 手势技能触发
        if current_gesture == "VICTORY" and now - skills["RESCUE"]["last_use"] > skills["RESCUE"]["cooldown"]:
            spawn_y = min(HEIGHT-50, player_y + 100)
            platforms.append((pygame.Rect(player_x - 30, spawn_y, PLATFORM_WIDTH, PLATFORM_HEIGHT), True, False, False))
            skills["RESCUE"]["last_use"] = now
        if current_gesture == "FIST" and now - skills["SHIELD"]["last_use"] > skills["SHIELD"]["cooldown"]:
            shield_active_end = now + 3.0
            skills["SHIELD"]["last_use"] = now
        if current_gesture == "PALM" and now - skills["BLAST"]["last_use"] > skills["BLAST"]["cooldown"]:
            hazards.clear()
            shockwave_radius = 1
            skills["BLAST"]["last_use"] = now

    # ------------------ 物理更新 ------------------
    if game_state == "PLAYING":
        player_x += (hand_target_x - player_x) * 0.2
        with lock: current_rms = volume_rms
        jump_force = 0.0

        if current_rms > VOLUME_THRESHOLD:
            raw_force = (current_rms - VOLUME_THRESHOLD) * volume_sensitivity_adjusted
            jump_force = min(25, raw_force)

        player_y += velocity_y
        player_rect = pygame.Rect(int(player_x), int(player_y), player_w, player_h)

        standing_on_platform = None
        is_on_bouncy_platform = False

        if velocity_y >= 0:
            velocity_y = min(velocity_y, 40)

            for i, (plat_rect, is_bouncing, is_broken, is_falling) in enumerate(platforms):
                if not is_falling and player_rect.colliderect(plat_rect) and abs(player_rect.bottom - plat_rect.top) < velocity_y + 20:
                    standing_on_platform = plat_rect
                    is_on_bouncy_platform = is_bouncing
                    if not is_bouncing and i != 0 and random.random() < 0.3:
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

        base_jump = -(10 + jump_force)

        # 【核心逻辑：解除控制锁定】
        if initial_drop and standing_on_platform:
            initial_drop = False  # 第一次踩到平台后，解锁控制
            velocity_y = -20      # 第一次自动弹起
            is_jumping = True

        elif standing_on_platform and jump_force > 1.0 and not is_jumping:
            velocity_y = base_jump * BOUNCE_MULTIPLIER if is_on_bouncy_platform else base_jump
            is_jumping = True

        if standing_on_platform and is_on_bouncy_platform and not is_jumping and jump_force < 1.0:
            velocity_y = -15
            is_jumping = True

        is_invincible = time.time() < shield_active_end
        for hazard_rect, _ in hazards[:]:
            if player_rect.colliderect(hazard_rect):
                if is_invincible:
                    hazards.remove((hazard_rect, _))
                    score += 50
                else: game_state = "GAME_OVER"

        # 只有在非 initial_drop 状态下才滚动地图，防止开局地板被卷走
        if not initial_drop and player_y < HEIGHT / 2.5:
            scroll_amt = (HEIGHT / 2.5) - player_y
            player_y += scroll_amt
            scroll += scroll_amt
            new_plats = []
            highest_y = HEIGHT
            for r, b, br, f in platforms:
                if f: r.y += PLATFORM_FALL_SPEED
                else: r.y += scroll_amt
                if r.bottom > 0:
                    new_plats.append((r, b, br, f))
                    if not f and r.y < highest_y: highest_y = r.y
            platforms = new_plats
            new_haz = []
            for r, v in hazards:
                r.y += scroll_amt
                if r.bottom > 0: new_haz.append((r, v))
            hazards = new_haz
            if len(platforms) < 15 or highest_y > 0:
                y = highest_y
                while y > -HEIGHT:
                    y -= random.randint(100, 180)
                    x = random.randint(0, WIDTH - PLATFORM_WIDTH)
                    is_b = random.random() < 0.25
                    platforms.append((pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT), is_b, False, False))
                if random.random() < 0.6: generate_hazard(highest_y)

        for i, (r, v) in enumerate(hazards):
            r.x += v
            if r.left < 0 or r.right > WIDTH: v = -v; hazards[i] = (r, v)
        score = int(scroll / 10)
        if player_y > HEIGHT: game_state = "GAME_OVER"

    # ------------------ 绘制 ------------------
    if bg_surface: screen.blit(bg_surface, (0, 0))
    else: screen.fill((20, 20, 30))
    screen.blit(dim_surface, (0, 0))

    if game_state == "PLAYING":
        for r, b, br, f in platforms:
            # 【绘制确认】如果 b (is_bouncing) 为 True，则使用橙色 (255,165,0)
            color = (80,80,80) if f else ((255,165,0) if b else (180,180,100))
            pygame.draw.rect(screen, color, r)
        for r, v in hazards:
            pygame.draw.circle(screen, (255, 50, 50), r.center, HAZARD_SIZE//2)

        if sprite_loaded and len(animation_frames) > 0:
            total_frames = len(animation_frames)
            if not is_jumping:
                current_frame_index = 0
            else:
                progress = max(0.0, min(1.0, (velocity_y + 15) / 30.0))
                air_count = total_frames - 1
                if air_count > 0:
                    current_frame_index = 1 + int(progress * (air_count - 1))
                else:
                    current_frame_index = 0
            if current_frame_index >= total_frames: current_frame_index = total_frames - 1
            char_img = animation_frames[current_frame_index]
            if hand_target_x < player_x - 5:
                 char_img = pygame.transform.flip(char_img, True, False)
            screen.blit(char_img, (int(player_x) - 4, int(player_y) - 4))
        else:
            pygame.draw.rect(screen, (200, 80, 120), (int(player_x), int(player_y), player_w, player_h))

        if time.time() < shield_active_end:
            pygame.draw.circle(screen, (255, 215, 0), (int(player_x + player_w/2), int(player_y + player_h/2)), 45, 3)
        if shockwave_radius > 0:
            shockwave_radius += 30
            pygame.draw.circle(screen, (0, 255, 255), (WIDTH//2, HEIGHT//2), shockwave_radius, 10)
            if shockwave_radius > WIDTH: shockwave_radius = 0

        ui_y = HEIGHT // 2 - 100
        for key, skill in skills.items():
            remaining = max(0, skill["cooldown"] - (now - skill["last_use"]))
            alpha = 100 if remaining > 0 else 255
            bg_rect = pygame.Rect(20, ui_y, 220, 50)
            s = pygame.Surface((220, 50)); s.set_alpha(alpha); s.fill((30, 30, 40))
            screen.blit(s, bg_rect)
            pygame.draw.rect(screen, skill["color"], bg_rect, 2)
            text = FONT.render(skill["name"], True, skill["color"])
            screen.blit(text, (30, ui_y + 15))
            if remaining > 0:
                time_text = FONT.render(f"{remaining:.1f}s", True, (150, 150, 150))
                screen.blit(time_text, (180, ui_y + 15))
            else:
                ready_text = FONT.render("READY", True, (255, 255, 255))
                screen.blit(ready_text, (180, ui_y + 15))
            ui_y += 60


        if not camera_available:
            no_cam_text = FONT.render("No Camera - Keyboard Mode", True, (255, 100, 100))
            screen.blit(no_cam_text, (WIDTH//2 - no_cam_text.get_width()//2, 20))

        vol_h = int(min(1.0, current_rms/0.02) * 200)
        pygame.draw.rect(screen, (50, 50, 50), (WIDTH-40, HEIGHT-250, 20, 200))
        pygame.draw.rect(screen, (0, 255, 0), (WIDTH-40, HEIGHT-50-vol_h, 20, vol_h))
        score_surf = BIG_FONT.render(str(score), True, (255, 255, 255))
        screen.blit(score_surf, (WIDTH//2 - score_surf.get_width()//2, 50))

    elif game_state == "START":
        title = BIG_FONT.render("SOUND JUMPER", True, (255, 255, 255))
        screen.blit(title, (WIDTH//2 - title.get_width()//2, HEIGHT//3))
        instr = ["RIGHT HAND: Move", "LEFT HAND: Gestures", "VOICE: Jump", "Press Key to Continue"] if camera_available else ["NO CAMERA", "A/D: Move", "1/2/3: Skills", "VOICE: Jump", "Press Key to Continue"]
        y = HEIGHT//2
        for line in instr:
            t = FONT.render(line, True, (200, 200, 200))
            screen.blit(t, (WIDTH//2 - t.get_width()//2, y)); y += 40

    elif game_state == "SETTINGS":
        title = BIG_FONT.render("SETTINGS", True, (255, 255, 255))
        screen.blit(title, (WIDTH//2 - title.get_width()//2, HEIGHT//4))
        setting_y = HEIGHT//2 - 50
        label = FONT.render(f"Voice Sensitivity: {int(volume_sensitivity_adjusted)}", True, (255, 255, 255))
        screen.blit(label, (WIDTH//2 - label.get_width()//2, setting_y))

        pygame.draw.rect(screen, (100,100,100), (WIDTH//2-200, setting_y+60, 400, 20))
        fill_w = int((volume_sensitivity_adjusted-500)/(8000-500)*400)
        pygame.draw.rect(screen, (0,255,100), (WIDTH//2-200, setting_y+60, fill_w, 20))

        start_text = FONT.render("Use Left/Right Arrows to Adjust", True, (200, 200, 200))
        screen.blit(start_text, (WIDTH//2 - start_text.get_width()//2, HEIGHT - 150))
        start_text = FONT.render("Press SPACE to Start", True, (100, 255, 100))
        screen.blit(start_text, (WIDTH//2 - start_text.get_width()//2, HEIGHT - 100))

    elif game_state == "GAME_OVER":
        t = BIG_FONT.render("GAME OVER", True, (255, 50, 50))
        screen.blit(t, (WIDTH//2 - t.get_width()//2, HEIGHT//3))
        s = BIG_FONT.render(f"Score: {score}", True, (255, 255, 255))
        screen.blit(s, (WIDTH//2 - s.get_width()//2, HEIGHT//2))
        r = FONT.render("Press Any Key to Continue", True, (200, 200, 200))
        screen.blit(r, (WIDTH//2 - r.get_width()//2, HEIGHT//2 + 80))

    pygame.display.flip()
    clock.tick(60)

if audio_stream: audio_stream.stop(); audio_stream.close()
if cap: cap.release()
pygame.quit()