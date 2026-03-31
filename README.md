# 群积分助手 (astrbot_plugin_point_system)

## 插件简介
`astrbot_plugin_point_system` 是一个面向 AstrBot 群聊场景的积分互动插件，围绕“签到、活跃、抽奖、兑换、管理”这几类高频玩法设计。它支持按群维护成员信息、自动保存数据、定时备份、日期口令奖励，以及负分限制和群头衔联动，适合做群活跃体系或轻量积分经济。

**版本：1.8.5**  
**展示名称：** `群积分助手`  
**GitHub 仓库：** [https://github.com/menglimi/astrbot_plugin_point_system](https://github.com/menglimi/astrbot_plugin_point_system)

---

## 支持平台
- AstrBot 插件环境
- QQ / AIOCQHTTP：功能最完整，支持头衔、设精、禁言、群头衔同步等能力
- 其他消息平台：可使用基础积分、签到、抽奖、排行等通用功能，但平台专属管理能力可能不可用

## 安装方式
1. 将插件目录放入 AstrBot 插件目录，例如 `C:\Users\用户名\.astrbot\data\plugins\astrbot_plugin_point_system`
2. 重启 AstrBot 或在插件管理中重新加载插件
3. 在 AstrBot 管理面板中根据 `_conf_schema.json` 调整配置
4. 首次运行后会自动在插件数据目录下生成积分数据文件
5. 插件目录附带了最小 [requirements.txt](C:/Users/99505/.astrbot/data/plugins/astrbot_plugin_point_system/requirements.txt) 说明文件；插件本身没有额外第三方 pip 依赖，运行依赖 AstrBot 主程序环境

---

## 核心功能
- 每日签到：支持固定积分或随机积分，支持首次签到奖励、每日首签奖励、连签奖励和每 7 天奖励
- 稀有彩蛋：签到时可低概率触发欧皇 / 非酋事件，自动加分或扣分
- 活跃奖励：群成员发送合规普通消息时，可静默获得积分
- 无前缀触发：支持“关键词+签到 / 签到+关键词”以及“关键词+抽奖 / 抽奖+关键词”
- 群内排行：优先展示当前群积分榜，群数据不足时回退全局排行
- 抽奖玩法：支持个人抽奖和群体抽奖，两种模式可独立开关
- 兑换玩法：支持积分兑换群头衔、设精消息和禁言
- 管理指令：支持独立管理员名单，允许通过 `@用户` 或直接输入 QQ 号增减积分
- 日期口令奖励：支持按日期、关键词、范围、概率发放奖励
- 生日系统：支持记录生日、生日签到奖励，以及按配置时间自动播报当日寿星名单
- 自动备份：支持多备份目标和每日定时备份
- 负分联动：负分用户仅可签到恢复积分，不能抽奖，并自动同步 `群女仆X号` 头衔

---

## 积分规则概览

### 获取积分
- 每日签到
- 每日首签额外奖励，按每天 `04:00` 刷新
- 首次签到额外奖励
- 连续签到加成与每 7 天节点奖励
- 群聊活跃奖励
- 日期口令奖励
- 生日签到奖励
- 定时生日名单播报
- 抽奖中奖返利

### 消耗积分
- 个人抽奖
- 群体抽奖报名
- 兑换头衔
- 兑换设精
- 兑换禁言
- 管理员手动扣分

### 负分规则
- 允许积分进入负数
- 负分用户只能通过每日签到恢复积分
- 负分用户无法参与抽奖
- 负分用户不会获得活跃奖励和日期口令奖励
- 在 QQ / AIOCQHTTP 群聊中，负分用户会自动同步 `群女仆X号` 头衔，转正后自动移除

---

## 插件流程
1. 插件启动时加载配置与本地积分数据，并自动兼容旧版数据结构
2. 群消息进入后，会先判断是否命中无前缀签到或无前缀抽奖口令
3. 如果是普通消息，则继续判断日期口令奖励和活跃奖励条件
4. 指令类消息由 `@filter.command` 处理签到、积分查询、排行榜、抽奖、兑换和管理操作
5. 所有积分变更都会在锁内更新并原子写入 `points_data.json`
6. 若启用自动备份，插件会在设定时间将数据文件备份到一个或多个目标地址

---

## 使用方法

### 普通用户指令
| 指令 | 说明 | 示例 |
| :--- | :--- | :--- |
| `/签到` | 进行每日签到 | `/签到` |
| `/我的积分` | 查询自己的当前积分 | `/我的积分` |
| `/积分榜` | 查看当前群积分排行 | `/积分榜` |
| `/积分规则` | 查看当前积分获取规则 | `/积分规则` |
| `/抽奖` | 按默认模式参与抽奖 | `/抽奖` |
| `/抽奖 个人` | 进行个人抽奖 | `/抽奖 个人` |
| `/抽奖 群体` | 参与群体抽奖报名 | `/抽奖 群体` |
| `/兑换头衔 头衔内容` | 兑换群头衔 | `/兑换头衔 肝帝` |
| `/兑换设精` | 引用消息后兑换设精 | `回复一条消息后发送 /兑换设精` |
| `/兑换禁言` | 兑换自禁言 | `/兑换禁言` |
| `/兑换禁言 @某用户` | 兑换禁言他人 | `需先开启 allow_mute_others` |
| `/记录生日 10/24` | 记录自己的生日 | `/记录生日 10/24` |
| `/生日签到` | 领取年度生日祝福与生日奖励；未记录时可按配置自动记录为今天 | `/生日签到` |

### 无前缀口令
签到和抽奖关键词都可在配置中单独修改。

| 口令 | 说明 |
| :--- | :--- |
| `<签到关键词>签到` / `签到<签到关键词>` | 无前缀签到 |
| `<抽奖关键词>抽奖` / `抽奖<抽奖关键词>` | 无前缀抽奖，直接走当前默认抽奖模式 |
| `<生日签到触发词>` | 无前缀生日签到，效果同 `/生日签到` |

### 管理员指令
| 指令 | 说明 | 示例 |
| :--- | :--- | :--- |
| `/给积分 @用户 100` | 为目标用户增加积分 | `/给积分 @某用户 100` |
| `/给积分 123456789 100` | 通过 QQ 号为目标用户增加积分 | `/给积分 123456789 100` |
| `/扣积分 @用户 50` | 扣除目标用户积分 | `/扣积分 @某用户 50` |
| `/扣积分 123456789 50` | 通过 QQ 号扣除目标用户积分 | `/扣积分 123456789 50` |
| `/清空所有数据 确认` | 清空全部积分、抽奖、生日与群记录 | `/清空所有数据 确认` |

管理员权限由 `admin_settings.points_admin_ids` 控制，只有配置过的 QQ 号可以增减积分。

---

## 配置说明
插件通过 `_conf_schema.json` 暴露配置项，下面按功能分组说明常用配置。

### 基础配置
- `points_name`：积分名称，可改为金币、贡献度等
- `message_templates.*`：消息模板配置
- `leaderboard_settings.display_limit`：排行榜显示数量
- `leaderboard_settings.show_self_rank`：是否显示自己的名次

### 签到配置
- `sign_in_settings.sign_in_mode`：`random` 或 `fixed`
- `sign_in_settings.fixed_sign_in_points`：固定签到积分
- `sign_in_settings.min_sign_in_points` / `max_sign_in_points`：随机签到范围
- `sign_in_settings.first_sign_in_bonus`：首次签到奖励
- `sign_in_settings.daily_first_sign_in_bonus`：每日首签额外奖励
- `sign_in_settings.streak_bonus_enabled`：是否启用连签奖励
- `sign_in_settings.streak_step_bonus`：连签每日递增奖励
- `sign_in_settings.streak_bonus_cap`：连签奖励上限
- `sign_in_settings.weekly_streak_bonus`：每连续 7 天额外奖励
- `sign_in_settings.fortune_event_enabled`：是否开启欧皇 / 非酋彩蛋
- `sign_in_settings.fortune_event_chance`：彩蛋触发概率
- `sign_in_settings.fortune_event_points`：彩蛋积分变化值
- `sign_in_settings.fortune_pity_enabled`：是否开启彩蛋保底
- `sign_in_settings.fortune_lucky_pity_threshold`：欧皇保底次数
- `sign_in_settings.fortune_unlucky_pity_threshold`：非酋保底次数

### 无前缀触发配置
- `sign_in_trigger`：旧版兼容用的完整签到口令
- `sign_in_trigger_keyword`：签到口令关键词，支持“关键词+签到”和“签到+关键词”
- `lottery_trigger_keyword`：抽奖口令关键词，支持“关键词+抽奖”和“抽奖+关键词”

### 生日配置
- `birthday_settings.enabled`：是否开启生日功能
- `birthday_settings.sign_in_trigger`：生日签到触发词
- `birthday_settings.reward_points`：生日签到奖励积分
- `birthday_settings.auto_record_when_unset`：未记录生日时是否自动记为当天
- `birthday_settings.auto_broadcast_enabled`：是否开启寿星名单定时播报
- `birthday_settings.auto_broadcast_time`：寿星名单播报时间，格式为 `HH:MM`

### 活跃奖励配置
- `activity_settings.enabled`：是否开启活跃奖励
- `activity_settings.points_per_message`：每次奖励积分
- `activity_settings.cooldown_seconds`：冷却时间
- `activity_settings.daily_limit`：每日奖励次数上限
- `activity_settings.min_text_length`：最短消息长度限制

### 抽奖配置
- `lottery_settings.enabled`：总开关
- `lottery_settings.default_mode`：默认抽奖模式，支持 `personal` 和 `group`
- `lottery_settings.personal_enabled` / `lottery_settings.group_enabled`：个人 / 群体抽奖开关
- `lottery_settings.personal_cost`：个人抽奖消耗积分
- `lottery_settings.personal_daily_limit`：个人抽奖每日次数
- `lottery_settings.personal_prizes.*`：个人抽奖五档奖项与概率权重
- `lottery_settings.group_cost`：群体抽奖报名积分
- `lottery_settings.group_daily_limit_per_user`：群体抽奖每人每日参与上限
- `lottery_settings.group_required_participants`：群体抽奖开奖人数
- `lottery_settings.group_distribution_ratios`：群体奖池分配比例

### 兑换配置
- `exchange_settings.title_enabled` / `title_cost` / `title_max_length`
- `exchange_settings.essence_enabled` / `essence_cost`
- `exchange_settings.mute_enabled` / `mute_cost` / `mute_duration_seconds`
- `exchange_settings.allow_mute_others`

### 管理配置
- `admin_settings.points_admin_ids`：积分管理员 QQ 列表
- `admin_settings.log_operations`：是否记录管理员操作日志
- `admin_settings.max_admin_give`：单次管理操作允许的最大加分值

### 日期口令奖励配置
- `special_date_reward_entries`：词条列表
- 单条词条支持 `name`、`enabled`、`priority`、`scope`、`dates`、`keywords`
- 单条词条支持 `reward_points`、`daily_limit_per_user`、`probability`
- 单条词条支持 `announce`、`reply_template`、`exact_match`

### 备份配置
- `backup_settings.enabled`：是否开启自动备份
- `backup_settings.backup_paths`：备份目标列表，支持目录和文件路径
- `backup_settings.auto_backup_time`：自动备份时间，格式为 `HH:MM`

### 负分提示配置
- `negative_settings.debt_message`：负分状态下尝试抽奖时显示的提示文案

---

## 数据文件
- 主数据文件：`<AstrBot数据目录>\plugin_data\astrbot_plugin_point_system\points_data.json`
- 备份文件：按配置写入 `backup_settings.backup_paths`
- 数据写入方式：锁保护 + 原子替换，减少异常退出时的损坏风险

---

## 注意事项
1. 兑换头衔、兑换设精、兑换禁言、负分头衔同步依赖 QQ / AIOCQHTTP 能力，其他平台可能无法生效
2. 机器人若没有对应群管理权限，兑换操作会失败；涉及先扣后调接口的场景会自动退款
3. 群体抽奖若当天未凑齐开奖人数，会在次日首次触发群体抽奖时自动退款
4. 无前缀抽奖会直接使用当前配置中的默认抽奖模式
5. 负分用户无法参与抽奖，也不会再获得活跃奖励或日期口令奖励
6. 备份地址填写目录时会自动生成时间戳文件，填写文件路径时会在文件名后追加时间戳
7. 请勿手动破坏 `points_data.json` 的编码和结构，插件默认使用 UTF-8 读写

---

## 运行与验证
当前目录不是 Git 仓库，且本地 Python 环境未安装 `astrbot` 模块，因此无法在当前终端完成完整运行态联调。当前已完成的本地验证如下：

- `python -m py_compile main.py birthday_feature.py lottery_feature.py`
- `_conf_schema.json` UTF-8 JSON 解析检查

---

## 更新记录

### 1.7.1
- 新增无前缀抽奖
- 签到和抽奖都支持“关键词在前 / 关键词在后”两种口令格式
- 无前缀关键词改为可配置

### 1.7.2
- 新增 `生日签到`，每位用户每年可领取一次生日祝福与生日奖励
- 负分状态下的抽奖拦截提示调整为债务 / 女仆装风格文案

### 1.8.0
- 新增 `/记录生日 mm/dd`，可手动记录生日
- 生日当天使用普通签到也会自动触发生日签到奖励
- 未记录生日时使用 `/生日签到` 或 `生日签到` 会自动将今天记为生日
- 每天按配置时间自动检查群内寿星并发送名单，没有寿星则不播报

### 1.8.1
- 去除生日功能和负分提示中的硬编码文案，改为配置驱动
- 文档中的无前缀触发词、生日奖励和播报时间改为通用占位说明
- 移除写死的 `星缘积分规则` 别名，避免品牌绑定

### 1.8.2
- 简化签到成功、重复签到和积分查询等高频提示
- 统一所有普通返回消息为单句输出，避免分段刷屏

### 1.8.3
- 修复非酋事件扣分仍被限制为最低 0 的问题，现在可以正确进入负分
- 将生日相关逻辑拆分到 `birthday_feature.py`
- 将抽奖相关逻辑拆分到 `lottery_feature.py`
- 同步修正作者与仓库地址信息

### 1.8.4
- 新增积分管理员指令 `/清空所有数据 确认`
- 清空时会重置全部积分、抽奖、生日和群记录
- 在 QQ / AIOCQHTTP 环境下会尽量先移除已同步的负分头衔

### 1.8.5
- 新增最小 `requirements.txt`
- 明确插件本身无额外第三方 pip 依赖，运行依赖 AstrBot 主程序环境

### 1.7.0
- 支持负分
- 负分用户限制为仅可签到恢复积分
- 自动同步 `群女仆X号` 头衔

### 1.6.x
- 增加每日首签奖励和 `04:00` 刷新
- 增加欧皇 / 非酋彩蛋与保底
- 增加自动备份
- 支持固定签到或随机签到

### 1.5.0
- 增加个人抽奖和群体抽奖
- 增加日期口令奖励词条

### 1.2.0
- 增加兑换头衔、设精、禁言

### 1.1.0
- 优化排行榜、存储和旧数据迁移

---

## 开发信息
1. 开发者：`menglimi`
2. 插件标识：`astrbot_plugin_point_system`
3. 展示名称：`群积分助手`
4. 仓库地址：[https://github.com/menglimi/astrbot_plugin_point_system](https://github.com/menglimi/astrbot_plugin_point_system)
5. 数据目录：`<AstrBot数据目录>\plugin_data\astrbot_plugin_point_system`
6. 当前终端环境：可完成语法与配置校验，暂不支持完整 AstrBot 运行态联调
