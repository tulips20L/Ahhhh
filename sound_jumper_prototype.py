import pygame
import numpy as np
import sounddevice as sd
import threading
import time
import random
import cv2
import mediapipe as mp

# ---------- 1. 初始化 & 屏幕设置 ----------
pygame.init()
pygame.mixer.init()

info = pygame.display.Info()
WIDTH, HEIGHT = info.current_w, info.current_h
screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.FULLSCREEN)
pygame.display.set_caption("Sound Jumper - Dual Hand Ver.")

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
    stream = sd.InputStream(channels=1, samplerate=SAMPLE_RATE,
                            blocksize=FRAME_SIZE, callback=audio_callback)
    stream.start()
    return stream

# ---------- 3. MediaPipe 手势识别设置 ----------
mp_hands = mp.solutions.hands
# 修改为 max_num_hands=2 以追踪双手
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2, 
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)
cap = cv2.VideoCapture(0)

# 简单的手势判断函数
def count_extended_fingers(hand_landmarks):
    """计算伸出的手指数量，并判断特定手势"""
    # 指尖索引: Thumb=4, Index=8, Middle=12, Ring=16, Pinky=20
    # 指节索引: Thumb=3, Index=6, Middle=10, Ring=14, Pinky=18
    tips = [4, 8, 12, 16, 20]
    pips = [3, 6, 10, 14, 18]
    
    extended = [0, 0, 0, 0, 0]
    
    # 拇指比较特殊，比较x坐标 (根据左右手不同，这里简化判断：只要指尖离手掌中心够远)
    # 为简化逻辑，我们主要判断另外四指
    
    # 食指到小指：如果指尖的y坐标小于指节y坐标 (屏幕坐标系y向下增大，所以"上"是更小)，则为伸出
    for i in range(1, 5):
        if hand_landmarks.landmark[tips[i]].y < hand_landmarks.landmark[pips[i]].y:
            extended[i] = 1
            
    # 拇指简单判断：如果指尖y小于指节y（向上），则为伸出
    if hand_landmarks.landmark[tips[0]].y < hand_landmarks.landmark[pips[0]].y:
        extended[0] = 1

    count = sum(extended)
    
    # 判定手势类型
    if count >= 4: return "PALM"  # 五指张开
    if count <= 1: return "FIST"  # 握拳
    if extended[1] and extended[2] and not extended[3] and not extended[4]: return "VICTORY" # 剪刀手
    
    return "UNKNOWN"

# ---------- 4. 游戏变量 ----------
clock = pygame.time.Clock()
FONT = pygame.font.SysFont(None, 30)
BIG_FONT = pygame.font.SysFont(None, 60)

player_w, player_h = 40, 40
player_x = WIDTH//2 - player_w//2
player_y = HEIGHT - 200
velocity_y = 0
gravity = 1  # 重力加速度（越大下落越快）
PLATFORM_FALL_SPEED = 20

# 音量参数
VOLUME_THRESHOLD = 0.003
VOLUME_SENSITIVITY = 2000
BOUNCE_MULTIPLIER = 2.0
volume_sensitivity_adjusted = VOLUME_SENSITIVITY  # 可调整的版本 

# 技能冷却系统
skills = {
    "RESCUE": {"cooldown": 5.0, "last_use": 0, "color": (255, 165, 0), "name": "Rescue (V-Sign)"},
    "SHIELD": {"cooldown": 8.0, "last_use": 0, "color": (255, 215, 0), "name": "Shield (Fist)"},
    "BLAST":  {"cooldown": 10.0, "last_use": 0, "color": (0, 255, 255), "name": "Blast (Palm)"}
}
shield_active_end = 0.0 # 护盾结束时间
shockwave_radius = 0 # 冲击波特效半径

# 平台与障碍
platforms = []
hazards = []
PLATFORM_WIDTH, PLATFORM_HEIGHT = 120, 15 
HAZARD_SIZE, HAZARD_SPEED = 15, 10

def generate_initial_platforms():
    global platforms
    platforms.clear()
    # 第一个平台在玩家正下方，用于垂直起跳
    platforms.append((pygame.Rect(WIDTH // 2 - 50, HEIGHT - 150, 100, 15), False, False, False))
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

# 状态变量
score = 0
is_jumping = False
scroll = 0
game_state = "START" 
hand_target_x = WIDTH // 2
settings_selected = 0  # 0=SENSITIVITY, 1=START_GAME 

# UI 资源
dim_surface = pygame.Surface((WIDTH, HEIGHT))
dim_surface.set_alpha(160)
dim_surface.fill((0, 0, 0))

audio_stream = start_audio_stream()

# 主循环
running = True
while running:
    # ================= CAMERA & HAND TRACKING =================
    success, image = cap.read()
    bg_surface = None
    
    current_gesture = "NONE"
    
    if success:
        image = cv2.flip(image, 1) # 镜像翻转
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = hands.process(image_rgb)
        
        h, w, c = image.shape
        
        if results.multi_hand_landmarks and results.multi_handedness:
            for idx, hand_landmarks in enumerate(results.multi_hand_landmarks):
                # 获取 MediaPipe 的左右手标签
                # 注意：因为我们做了 cv2.flip，所以 MediaPipe 的 "Left" 其实是用户的 "Right" (屏幕右侧)
                label = results.multi_handedness[idx].classification[0].label
                
                # 计算手掌中心X坐标 (0~1)
                hand_cx = hand_landmarks.landmark[9].x
                
                # 逻辑：屏幕右侧的手 (label=="Left") 控制移动，屏幕左侧的手 (label=="Right") 控制技能
                
                if label == "Left": # 这是用户的右手，在屏幕右侧 -> 移动控制
                    target_raw = hand_cx * WIDTH
                    # 稍微加点偏移，让手的位置更自然对应屏幕中心
                    hand_target_x = max(0, min(WIDTH - player_w, target_raw - player_w/2))
                    
                    # 视觉反馈：绿色点
                    cv2.circle(image, (int(hand_cx*w), int(hand_landmarks.landmark[9].y*h)), 15, (0, 255, 0), -1)
                    
                elif label == "Right": # 这是用户的左手，在屏幕左侧 -> 技能控制
                    gesture = count_extended_fingers(hand_landmarks)
                    current_gesture = gesture
                    
                    # 视觉反馈：显示识别到的手势文字
                    cv2.putText(image, gesture, (int(hand_cx*w)-40, int(hand_landmarks.landmark[9].y*h)-40), 
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 3)
                    cv2.circle(image, (int(hand_cx*w), int(hand_landmarks.landmark[9].y*h)), 15, (0, 255, 255), -1)

        # 渲染背景
        bg_image = cv2.resize(image, (WIDTH, HEIGHT))
        bg_surface = pygame.image.frombuffer(bg_image.tobytes(), bg_image.shape[1::-1], "RGB")

    # ================= INPUT & SKILLS =================
    for event in pygame.event.get():
        if event.type == pygame.QUIT: running = False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE: running = False
            
            if game_state == "SETTINGS":
                if event.key == pygame.K_LEFT:
                    volume_sensitivity_adjusted = max(500, volume_sensitivity_adjusted - 200)
                elif event.key == pygame.K_RIGHT:
                    volume_sensitivity_adjusted = min(4000, volume_sensitivity_adjusted + 200)
                elif event.key == pygame.K_RETURN or event.key == pygame.K_SPACE:
                    # Start Game
                    player_vx = velocity_y = 0
                    score = scroll = 0
                    is_jumping = False
                    generate_initial_platforms()
                    hazards.clear()
                    player_x, player_y = WIDTH//2, HEIGHT-200
                    skills["RESCUE"]["last_use"] = 0
                    skills["SHIELD"]["last_use"] = 0
                    skills["BLAST"]["last_use"] = 0
                    game_state = "PLAYING"
            
            elif game_state == "START":
                game_state = "SETTINGS"
            
            elif game_state == "GAME_OVER":
                game_state = "SETTINGS"

    # 技能触发逻辑
    now = time.time()
    if game_state == "PLAYING":
        # 1. 剪刀手 -> 召唤平台
        if current_gesture == "VICTORY" and now - skills["RESCUE"]["last_use"] > skills["RESCUE"]["cooldown"]:
            # 在脚下生成弹跳平台
            spawn_y = min(HEIGHT-50, player_y + 100)
            platforms.append((pygame.Rect(player_x - 30, spawn_y, PLATFORM_WIDTH, PLATFORM_HEIGHT), True, False, False))
            skills["RESCUE"]["last_use"] = now
            # 音效 placeholder

        # 2. 握拳 -> 护盾
        if current_gesture == "FIST" and now - skills["SHIELD"]["last_use"] > skills["SHIELD"]["cooldown"]:
            shield_active_end = now + 3.0
            skills["SHIELD"]["last_use"] = now
        
        # 3.手掌 -> 冲击波
        if current_gesture == "PALM" and now - skills["BLAST"]["last_use"] > skills["BLAST"]["cooldown"]:
            hazards.clear() # 清除所有障碍
            shockwave_radius = 1 # 触发动画
            skills["BLAST"]["last_use"] = now

    # ================= UPDATE LOGIC =================
    if game_state == "PLAYING":
        # 平滑移动
        player_x += (hand_target_x - player_x) * 0.2
        
        # 获取音量
        with lock: current_rms = volume_rms
        
        jump_force = 0.0
        if current_rms > VOLUME_THRESHOLD:
            jump_force = min(18, (current_rms - VOLUME_THRESHOLD) * volume_sensitivity_adjusted)

        # 物理更新
        player_y += velocity_y
        player_rect = pygame.Rect(int(player_x), int(player_y), player_w, player_h)
        
        # 平台碰撞
        standing_on_platform = None
        is_on_bouncy_platform = False
        if velocity_y >= 0:
            for i, (plat_rect, is_bouncing, is_broken, is_falling) in enumerate(platforms):
                if not is_falling and player_rect.colliderect(plat_rect) and abs(player_rect.bottom - plat_rect.top) < velocity_y + 10:
                    standing_on_platform = plat_rect
                    is_on_bouncy_platform = is_bouncing
                    # 30% 破碎逻辑
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

        # 跳跃
        base_jump = -(8 + jump_force)
        if standing_on_platform and jump_force > 1.0 and not is_jumping:
            velocity_y = base_jump * BOUNCE_MULTIPLIER if is_on_bouncy_platform else base_jump
            is_jumping = True
        
        # 弹跳平台自动弹
        if standing_on_platform and is_on_bouncy_platform and not is_jumping and jump_force < 1.0:
            velocity_y = -15
            is_jumping = True

        # 障碍物碰撞
        is_invincible = time.time() < shield_active_end
        for hazard_rect, _ in hazards[:]:
            if player_rect.colliderect(hazard_rect):
                if is_invincible:
                    # 护盾状态：撞碎障碍物
                    hazards.remove((hazard_rect, _))
                    score += 50 # 奖励分
                else:
                    game_state = "GAME_OVER"

        # 滚动屏幕
        if player_y < HEIGHT / 2.5:
            scroll_amt = (HEIGHT / 2.5) - player_y
            player_y += scroll_amt
            scroll += scroll_amt
            
            # 更新平台位置
            new_plats = []
            highest_y = HEIGHT
            for r, b, br, f in platforms:
                if f: r.y += PLATFORM_FALL_SPEED
                else: r.y += scroll_amt
                if r.bottom > 0:
                    new_plats.append((r, b, br, f))
                    if not f and r.y < highest_y: highest_y = r.y
            platforms = new_plats
            
            # 更新障碍
            new_haz = []
            for r, v in hazards:
                r.y += scroll_amt
                if r.bottom > 0: new_haz.append((r, v))
            hazards = new_haz
            
            # 生成新地形
            if len(platforms) < 15 or highest_y > 0:
                y = highest_y
                while y > -HEIGHT:
                    y -= random.randint(100, 180)
                    x = random.randint(0, WIDTH - PLATFORM_WIDTH)
                    is_b = random.random() < 0.25
                    platforms.append((pygame.Rect(x, y, PLATFORM_WIDTH, PLATFORM_HEIGHT), is_b, False, False))
                if random.random() < 0.6: generate_hazard(highest_y)

        # 障碍移动
        for i, (r, v) in enumerate(hazards):
            r.x += v
            if r.left < 0 or r.right > WIDTH:
                v = -v
                hazards[i] = (r, v)

        score = int(scroll / 10)
        if player_y > HEIGHT: game_state = "GAME_OVER"

    # ================= DRAWING =================
    if bg_surface: screen.blit(bg_surface, (0, 0))
    screen.blit(dim_surface, (0, 0))

    if game_state == "PLAYING":
        # 绘制平台
        for r, b, br, f in platforms:
            color = (80,80,80) if f else ((255,165,0) if b else (180,180,100))
            pygame.draw.rect(screen, color, r)
        
        # 绘制障碍
        for r, v in hazards:
            pygame.draw.circle(screen, (255, 50, 50), r.center, HAZARD_SIZE//2)
        
        # 绘制角色
        pygame.draw.rect(screen, (200, 80, 120), (int(player_x), int(player_y), player_w, player_h))
        
        # 绘制护盾特效
        if time.time() < shield_active_end:
            pygame.draw.circle(screen, (255, 215, 0), (int(player_x + player_w/2), int(player_y + player_h/2)), 45, 3)

        # 绘制冲击波特效
        if shockwave_radius > 0:
            shockwave_radius += 30
            pygame.draw.circle(screen, (0, 255, 255), (WIDTH//2, HEIGHT//2), shockwave_radius, 10)
            if shockwave_radius > WIDTH: shockwave_radius = 0

        # 绘制左侧技能栏 (HUD)
        ui_y = HEIGHT // 2 - 100
        for key, skill in skills.items():
            # 计算冷却进度
            remaining = max(0, skill["cooldown"] - (now - skill["last_use"]))
            alpha = 100 if remaining > 0 else 255
            
            # 图标背景
            bg_rect = pygame.Rect(20, ui_y, 220, 50)
            s = pygame.Surface((220, 50))
            s.set_alpha(alpha)
            s.fill((30, 30, 40))
            screen.blit(s, bg_rect)
            pygame.draw.rect(screen, skill["color"], bg_rect, 2)
            
            # 技能名
            text = FONT.render(skill["name"], True, skill["color"])
            screen.blit(text, (30, ui_y + 15))
            
            # 冷却时间/就绪提示
            if remaining > 0:
                time_text = FONT.render(f"{remaining:.1f}s", True, (150, 150, 150))
                screen.blit(time_text, (160, ui_y + 15))
            else:
                ready_text = FONT.render("READY", True, (255, 255, 255))
                screen.blit(ready_text, (160, ui_y + 15))
            
            ui_y += 70

        # 音量条
        vol_h = int(min(1.0, current_rms/0.02) * 200)
        pygame.draw.rect(screen, (50, 50, 50), (WIDTH-40, HEIGHT-250, 20, 200))
        pygame.draw.rect(screen, (0, 255, 0), (WIDTH-40, HEIGHT-50-vol_h, 20, vol_h))

        # 分数
        score_surf = BIG_FONT.render(str(score), True, (255, 255, 255))
        screen.blit(score_surf, (WIDTH//2 - score_surf.get_width()//2, 50))

    elif game_state == "START":
        title = BIG_FONT.render("DUAL HAND SOUND JUMPER", True, (255, 255, 255))
        screen.blit(title, (WIDTH//2 - title.get_width()//2, HEIGHT//3))
        
        instr = [
            "RIGHT HAND: Move Left/Right",
            "LEFT HAND GESTURES:",
            "  [V-Sign] Rescue Platform",
            "  [Fist]   Shield (Invincible)",
            "  [Palm]   Clear Screen",
            "VOICE: Scream to JUMP!",
            "Press Any Key to Continue"
        ]
        y = HEIGHT//2
        for line in instr:
            t = FONT.render(line, True, (200, 200, 200))
            screen.blit(t, (WIDTH//2 - t.get_width()//2, y))
            y += 40

    elif game_state == "SETTINGS":
        title = BIG_FONT.render("SETTINGS", True, (255, 255, 255))
        screen.blit(title, (WIDTH//2 - title.get_width()//2, HEIGHT//4))
        
        # Volume Sensitivity Slider
        setting_y = HEIGHT//2 - 50
        label = FONT.render("Voice Sensitivity (← → to adjust):", True, (255, 255, 255))
        screen.blit(label, (WIDTH//2 - label.get_width()//2, setting_y))
        
        # Draw slider
        slider_width = 400
        slider_x = WIDTH//2 - slider_width//2
        slider_y = setting_y + 60
        
        # Slider background
        pygame.draw.rect(screen, (100, 100, 100), (slider_x, slider_y, slider_width, 20))
        
        # Slider fill (based on sensitivity value)
        fill_width = int((volume_sensitivity_adjusted - 500) / (4000 - 500) * slider_width)
        pygame.draw.rect(screen, (0, 255, 100), (slider_x, slider_y, fill_width, 20))
        
        # Slider value display
        value_text = FONT.render(f"Sensitivity: {volume_sensitivity_adjusted}", True, (255, 200, 100))
        screen.blit(value_text, (WIDTH//2 - value_text.get_width()//2, slider_y + 40))
        
        # Sensitivity descriptions
        desc_y = slider_y + 100
        descriptions = [
            "← Lower = Need more voice to jump",
            "→ Higher = Less voice needed to jump"
        ]
        for desc in descriptions:
            desc_text = FONT.render(desc, True, (150, 150, 150))
            screen.blit(desc_text, (WIDTH//2 - desc_text.get_width()//2, desc_y))
            desc_y += 30
        
        # Start game instruction
        start_text = FONT.render("Press ENTER or SPACE to Start Game", True, (100, 255, 100))
        screen.blit(start_text, (WIDTH//2 - start_text.get_width()//2, HEIGHT - 100))

    elif game_state == "GAME_OVER":
        t = BIG_FONT.render("GAME OVER", True, (255, 50, 50))
        screen.blit(t, (WIDTH//2 - t.get_width()//2, HEIGHT//3))
        s = BIG_FONT.render(f"Score: {score}", True, (255, 255, 255))
        screen.blit(s, (WIDTH//2 - s.get_width()//2, HEIGHT//2))
        r = FONT.render("Press Any Key to Settings", True, (200, 200, 200))
        screen.blit(r, (WIDTH//2 - r.get_width()//2, HEIGHT//2 + 80))

    pygame.display.flip()
    clock.tick(60)

# 清理
audio_stream.stop()
audio_stream.close()
cap.release()
pygame.quit()