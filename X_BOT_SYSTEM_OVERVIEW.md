# ⚔️ X-WAR DISCORD BOT - 完整系统架构与运作指南 (System Overview & Architecture)

> **文档目的**: 本文档专为服务器管理层（Administration）及 AI 架构顾问设计，全面阐述 X-War 机器人的功能模块、社群进阶流程、经济数值体系、动态包厢机制、沉寂净化算法与运维规范。

---

## 目录
1. [服务器旅程与权限层级 (Onboarding & Roles)](#1-服务器旅程与权限层级)
2. [关键频道映射表 (Channel Directory)](#2-关键频道映射表)
3. [双币经济体系 (Dual-Currency Economy Rebalance)](#3-双币经济体系)
4. [动态服务包厢系统 (Server Lounges System)](#4-动态服务包厢系统)
5. [私人定制套房 (Private Suites)](#5-私人定制套房)
6. [死亡禁区与净化机制 (Deadzone Crypt & Purge)](#6-死亡禁区与净化机制)
7. [管理员与运维面板 (Staff & Admin Tools)](#7-管理员与运维面板)

---

## 1. 服务器旅程与权限层级

服务器采取严格的漏斗式新人引导与等级解锁机制，杜绝广告号与炸服风险：

```
[新成员进服]
    │
    ▼ (仅可见 #rules 与 #verify)
[点击 🔒 Verify] 
    │
    ▼
[获取 Guest 身份组] 
    │  • 解锁基础聊天区域
    │  • 通过发言互动赚取经验值 (XP)
    ▼
[达到 Level 2] 
    │  • 自动晋级为 Member 身份组
    ▼
[Member 权限]
    │  • 解锁 #gaming-lobby 与游戏专属频道
    │  • 在身份组面板自助勾选设备与游戏标签
```

### 身份组梯队 (Role Hierarchy)
- **Unverified (`@everyone`)**: 仅能查阅规则频道与验证频道。
- **Guest**: 通过按键验证，进入新手观察期。
- **Member**: 达到活跃等级 2 级后自动解锁，可自由加入游戏大厅和选择身份标签。
- **Private Suite Holder**: 达到 Level 10+ 提交申请、由管理层通过审核的私人套房拥有者。
- **Administrator / Staff**: 拥有全局运维面板、强制清理、免限制延时等管理特权。

---

## 2. 关键频道映射表

机器人内已绑定以下核心功能频道的系统 ID：

| 频道名称 | 频道 ID | 主要用途与常驻交互 |
| :--- | :--- | :--- |
| **`#rules`** | `1527302732522979379` | 服务器守则常驻说明 |
| **`#verify`** | `1531906544587902976` | `[ 🔒 Verify ]` 永久一键验证面板 |
| **`#welcome`** | `1531583060368162826` | 欢迎卡片与新玩家指引 |
| **`#bot-notification`** | `1526521131048370217` | 机器人系统事件、跨系统升级广播 |
| **`#deadzone-crypt`** | `1551840699631272006` | 阵亡名单公布、复活与假释交互面板 |
| **`#deadzone-notification`** | `1554761264566116412` | 沉寂玩家被移入墓园/被净化的通知日志 |
| **`#lounge-hub`** | 动态绑定 | 开房大厅面板 (动态创建/预订 5 间包厢) |

---

## 3. 双币经济体系

经济系统全面重平衡，确立 **Cash (大面额高流通游戏币)** 与 **XC (极度稀有专属货币)** 的双币体系：

### 3.1 货币定位与获取
- **Cash (游戏币)**:
  - **初始赠送**: `$50,000` (50k)
  - **常规流通**: 聊天奖励、博彩（Coinflip、Roulette、Blackjack、Slots）高额下注（支持 10k ~ 1M 下注）。
  - **每日签到 (`/daily`)**: 基础 `$10,000 Cash` + 签到连胜加成。
- **XC (X-Coin 珍稀代币)**:
  - **定位**: 服务器核心稀缺硬通货，用于兑换顶级特权与永久身份。
  - **获取途径**: 每日签到固定获取 `50 XC`；可在银行以 `10,000 Cash = 1 XC` 汇率定向兑换。

### 3.2 专属特权商城 (`/shop` -> 👑 VIP & Perks)
1. **👑 Custom Tag (自定义头衔)**:
   - 售价: `2,500,000 Cash` 或 `250 XC`
   - 特权: 允许玩家在服务器内定制专属彩色身份组与个性前缀。
2. **💎 VIP Lounge Pass (贵宾包厢通行证)**:
   - 售价: `5,000,000 Cash` 或 `500 XC`
   - 特权: 彻底解除 Server Lounge 包厢的 5 小时延时硬上限（每次延时 +30 分钟，不限次数）。

---

## 4. 动态服务包厢系统 (Server Lounges)

专为开黑与战队战术讨论设计的 5 间动态专属包厢（文字频道 + 语音频道组）：

### 4.1 隐蔽与预订机制 (Host-Only Dynamic Visibility)
- **空闲状态 (Idle)**: 包厢对 `@everyone` 默认**完全隐藏**，保持频道列表清爽极简。
- **房主预订 (Booking)**: 玩家在 `#lounge-hub` 点击预订后，系统唤醒对应包厢，房主与受邀组员获得查阅权限。
- **隐私切换**: 房主可在包厢控制面板中一键切换为 **Public (全服可见)** 或 **Private (白名单成员可见)**。

### 4.2 智能延时规则 (Extension Rules)
- **普通用户**: 单次开房默认 2 小时，可按需延长，受最大时长 5 小时上限限制。
- **VIP & 管理员特权**: 
  - **管理员 (Administrator)** 或 持有 **VIP Lounge Pass** 的玩家，点击 `[ ⏳ Extend (+30m) ]` 时**自动绕过 5 小时硬上限**，享受无限制加时。

### 4.3 自动化生命周期与战队集结
- **自动回收**: 当语音频道人去楼空或租期耗尽时，系统执行自动清理：清空历史聊天记录、重置权限、隐藏频道。
- **战队呼叫 (Squad Ping)**: 房主可一键广播正在游玩的特定游戏（如 *Valorant*, *Apex*, *Delta Force*），吸引同好加入。

---

## 5. 私人定制套房 (Private Suites)

- **申请门槛**: 全服等级达到 **Level 10+** 的常驻核心成员。
- **申请流程**: 
  - 玩家提交名称、主题与房型需求。
  - **Level 20+** 高级玩家自动打上 `Senior Veteran` 标签优先加急。
- **管理层审核**: 申请自动推送至管理端，管理员在交互式卡片上一键执行 `[ Approve ]` 或 `[ Deny ]`。

---

## 6. 死亡禁区沉寂与复活机制 (Deadzone Crypt & 1+2 Respawn)

维护服务器活跃度、排查沉寂用户的核心防潜水与趣味互动系统：

```
[连续 7 天无发言 / 无进入语音]
       │
       ▼ (后台定时轮询或管理员扫描触发)
[移入 Deadzone Crypt (冷冻休眠)] 
       │  • 暂时扣留 Member、Music/Premium Music 及等级称号 (Active/Elite 等)
       │  • 赋予 Deadzone 专属身份组并记录原权限档案
       │  • 向 #bot-notification 发布墓碑立碑通报 [TOMBSTONE ERECTED]
       ▼
[执行 1+2 复活协议 (1+2 Respawn Protocol)]
  ├── 步骤 1 (解冻 Thaw Out - 5/5):
  │     • 文字频道聊天: 发送 5 条消息 (每条 +1)
  │     • 语音包厢挂机: 在 Lounge 1~5 语音每待 3 分钟自动 +1 解冻
  │     • 附带幽灵玩法: 可在墓园执行 `/deadzone haunt` 呼唤活人并赚取 +30 XC
  └── 步骤 2 (战友营救 Teammate Rescue):
        • 解冻达成 5/5 后，由在世战友执行 `/deadzone rescue member:@沉睡者`
        • 或在 #deadzone-crypt 展板点击 [ 🤝 Rescue Teammate ] 一键打捞
       │
       ▼ (营救成功 / 复活完成)
[全服欢庆与奖励结算]
  ├── 本人复活: 完璧归还原有等级与身份组，获赠 +$100,000 Cash、+150 XC、+100 XP
  ├── 救人战友 (Hero Bounty): 获赠 +$50,000 Cash、+250 XC、+150 XP
  └── 5 分钟全服欢迎派对 (Welcome Party): 在主频道开启派对，前来打招呼的玩家每人领取 +$2,000 Cash!
```

### 6.1 核心配置参数 (System Configuration & Defaults)
| 配置键名 (DB Key) | 默认值 | 作用说明 |
| :--- | :--- | :--- |
| `deadzone_enabled` | `1` | 是否开启死亡禁区全套自动化系统 (0: 关闭, 1: 开启) |
| `deadzone_days` | `7` 天 | 判定为沉寂不活跃的天数阈值 (默认 7 天无互动触发降级) |
| `deadzone_role_id` | `1551839505168859196` | 死亡禁区专属身份组 ID (Deadzone) |
| `deadzone_notification_channel_id` | `1526521131048370217` | 墓碑立碑、解冻完成、复活公告推送频道 (`#bot-notification`) |
| `deadzone_party_channel_id` | `1524716540988231820` | 复活后 5 分钟欢迎派对发起频道 (`#general`) |
| `deadzone_crypt_channel_id` | `1551840699631272006` | 死亡禁区常驻展板频道 (`#deadzone-crypt`) |
| `deadzone_revive_bonus_cash` | `$100,000` | 沉睡者复活后获得的现金奖励 |
| `deadzone_revive_bonus_xc` | `150 XC` | 沉睡者复活后获得的稀有 XC 奖励 |
| `deadzone_revive_bonus_xp` | `100 XP` | 沉睡者复活后获得的经验加成 |
| `deadzone_rescue_reward_cash`| `$50,000` | 营救战友的玩家获得的现金赏金 |
| `deadzone_rescue_reward_xc`  | `250 XC` | 营救战友的玩家获得的稀有 XC 赏金 |
| `deadzone_rescue_reward_xp`  | `150 XP` | 营救战友的玩家获得的经验赏金 |
| `deadzone_haunt_reward_xc`   | `30 XC` | 处于禁区内的成员使用 `/deadzone haunt` 获得的奖励 |
| `deadzone_party_duration`    | `300` 秒 (5分钟) | 复活派对持续时间 |
| `deadzone_party_reward_cash` | `$2,000` | 派对期间进群发言打招呼成员获得的单次红包 |

### 6.2 玩家指令体系 (Player Slash Commands)
- `/deadzone status`: 查看服务器当前沉睡人数、自身休眠/活跃状态与历史复活次数。
- `/deadzone scavenge`: 每日（冷却 20 小时）探索死亡禁区废墟拾荒，获得 `35 ~ 85 XC`。
- `/deadzone haunt [target]`: 仅限禁区沉睡者使用（冷却 2 小时），向活着的好友发出哀鸣幽灵传讯，赚取 `+30 XC`。
- `/deadzone wake [member]`: 呼唤或私信提醒沉睡战友醒来（管理员使用可直接强行唤醒）。
- `/deadzone rescue [member]`: 营救已达成 5/5 解冻进度的战友，领取丰厚 Hero 赏金。

### 6.3 管理员指令体系 (Staff & Admin Commands)
- `/deadzone restore member:@user` 或 `/deadzone revive member:@user`: 强制唤醒沉睡者并恢复全套身份与奖励。
- `/deadzone send member:@user reason:...`: 手动将指定玩家移入死亡禁区。
- `/deadzone scan` (或 `/admin` -> Deadzone): 扫描全服 7 天未发言成员并批量移送。
- `/deadzone party [member]`: 手动在聊天频道开启 5 分钟欢迎派对。
- `/deadzone set_channel channel_type:... channel:#...`: 配置通知/派对/展板频道。


---

## 7. 管理员与运维面板 (Staff & Admin Tools)

管理员可通过 `/admin` 启动多合一交互式运维中枢：

1. **面板部署 (Panels Hub)**:
   - 一键将全英文统一的 **3x3 设备/游戏身份组面板 (Roles Panel)** 推送到指定频道。
   - 一键部署 **验证面板 (Verify Panel)** 与 **规则说明 (Rules)**。
   - 一键部署 **包厢预订中枢 (Lounge Booking Hub)**。
2. **批量运维与安全**:
   - 沉寂名单扫描与手动净化调试。
   - 经济数据查询、调整与日志回溯。
   - 私人套房审批控制台。

---
*Generated by Antigravity Assistant for X-War Community Administration.*
