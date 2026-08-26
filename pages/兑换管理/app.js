(() => {
  "use strict";

  const DEFAULT_TEMPLATE = "兑换成功！\n兑换物：{item}\n兑换内容：{content}\n消耗 {cost} {points_name}，剩余 {remaining} {points_name}。";
  const DEFAULT_SCOPE = { mode: "blacklist", scope: [] };
  const state = { data: null, draft: [], scope: { ...DEFAULT_SCOPE }, settingsDraft: {}, selected: -1, dirty: false, settingsDirty: false, saving: false, settingsSaving: false, saveStatus: "clean", page: "overview", view: "inventory", theme: "system", groupId: "", groupQuery: "", historyRange: "7d", dashboardLoading: false, dashboardRequest: 0 };
  let saveStateTimer = 0;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  const uniqueLines = (value) => {
    const values = Array.isArray(value)
      ? value
      : String(value || "").split(/\r?\n/);
    return [...new Set(values.map((item) => String(item || "").trim()).filter(Boolean))];
  };
  const uniqueScopeLines = (value) => {
    const seen = new Set();
    return String(value || "").replace(/，/g, ",").split(/[\r\n,]+/).map((item) => item.trim()).filter((item) => {
      const normalized = item.toLocaleLowerCase();
      if (!normalized || seen.has(normalized)) return false;
      seen.add(normalized);
      return true;
    });
  };
  const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });
  const compactNumber = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 });

  const SETTINGS_SECTIONS = [
    { id: "basic", label: "基础设置", icon: "badge-info", description: "积分名称和无前缀快捷触发词。", fields: [
      { path: "points_name", label: "积分名称", hint: "用于所有指令回复与页面单位", type: "text" },
      { path: "sign_in_trigger_keyword", label: "签到关键词", hint: "支持“关键词+签到”和“签到+关键词”", type: "text" },
      { path: "lottery_trigger_keyword", label: "抽奖关键词", hint: "支持“关键词+抽奖”和“抽奖+关键词”", type: "text" },
    ] },
    { id: "signin", label: "签到奖励", icon: "calendar-check-2", description: "设置基础签到积分、首次奖励、运势事件和连签加成。", fields: [
      { path: "sign_in_settings.sign_in_mode", label: "签到积分模式", hint: "固定积分或区间随机", type: "select", options: [["random", "区间随机"], ["fixed", "固定积分"]] },
      { path: "sign_in_settings.fixed_sign_in_points", label: "固定签到积分", hint: "固定模式下每次发放", type: "number", min: 0 },
      { path: "sign_in_settings.min_sign_in_points", label: "随机积分下限", hint: "随机模式的最小值", type: "number", min: 0 },
      { path: "sign_in_settings.max_sign_in_points", label: "随机积分上限", hint: "随机模式的最大值", type: "number", min: 0 },
      { path: "sign_in_settings.first_sign_in_bonus", label: "首次签到奖励", hint: "用户第一次签到的额外奖励", type: "number", min: 0 },
      { path: "sign_in_settings.daily_first_sign_in_bonus", label: "每日首签奖励", hint: "群内每天第一位签到用户的额外奖励", type: "number", min: 0 },
      { path: "sign_in_settings.fortune_event_enabled", label: "运势事件", hint: "签到时启用低概率额外奖惩", type: "boolean" },
      { path: "sign_in_settings.fortune_event_chance", label: "运势触发概率", hint: "0 到 1，例如 0.01 表示 1%", type: "number", min: 0, max: 1, step: 0.001 },
      { path: "sign_in_settings.fortune_event_points", label: "运势事件积分", hint: "触发时增加或扣除的积分", type: "number", min: 0 },
      { path: "sign_in_settings.streak_bonus_enabled", label: "连签加成", hint: "连续签到时增加额外积分", type: "boolean" },
      { path: "sign_in_settings.streak_step_bonus", label: "连签步进奖励", hint: "每连续一天增加的奖励", type: "number", min: 0 },
      { path: "sign_in_settings.streak_bonus_cap", label: "连签奖励上限", hint: "每日连签额外奖励封顶", type: "number", min: 0 },
      { path: "sign_in_settings.weekly_streak_bonus", label: "每周连签奖励", hint: "每连续 7 天额外增加", type: "number", min: 0 },
      { path: "sign_in_settings.make_up_cost", label: "补签消耗", hint: "发送 /补签 时消耗的积分，填 0 表示免费", type: "number", min: 0 },
      { path: "sign_in_settings.make_up_monthly_limit", label: "每月补签上限", hint: "每个用户每月最多补签次数，填 0 表示不限次数", type: "number", min: 0 },
    ] },
    { id: "activity", label: "活跃奖励", icon: "messages-square", description: "按消息活跃度自动发放积分。", fields: [
      { path: "activity_settings.enabled", label: "启用活跃奖励", hint: "群聊消息达到条件后自动获分", type: "boolean" },
      { path: "activity_settings.points_per_message", label: "每次奖励积分", hint: "通过一次活跃判定发放的积分", type: "number", min: 1 },
      { path: "activity_settings.cooldown_seconds", label: "奖励冷却时间", hint: "同一用户两次奖励的最短间隔（秒）", type: "number", min: 0 },
      { path: "activity_settings.daily_limit", label: "每日奖励次数", hint: "填 0 表示当天不发放", type: "number", min: 0 },
      { path: "activity_settings.min_text_length", label: "最短消息长度", hint: "少于该长度的消息不计活跃", type: "number", min: 1 },
    ] },
    { id: "steal", label: "偷积分玩法", icon: "hand-coins", description: "控制群内偷积分的次数、范围、概率和失败惩罚。", fields: [
      { path: "steal_settings.enabled", label: "启用偷积分", hint: "允许成员使用 /偷积分 @用户", type: "boolean" },
      { path: "steal_settings.daily_steal_limit", label: "每日可偷次数", hint: "填 0 表示不限次数", type: "number", min: 0 },
      { path: "steal_settings.daily_be_stolen_limit", label: "每日可被偷次数", hint: "按成功被偷次数计算，填 0 表示不限", type: "number", min: 0 },
      { path: "steal_settings.min_points", label: "偷取积分下限", hint: "成功时随机获得的最少积分", type: "number", min: 1 },
      { path: "steal_settings.max_points", label: "偷取积分上限", hint: "成功时随机获得的最多积分", type: "number", min: 1 },
      { path: "steal_settings.success_probability", label: "偷取成功概率", hint: "0 到 1，例如 0.5 表示 50%", type: "number", min: 0, max: 1, step: 0.01 },
      { path: "steal_settings.failure_cost", label: "失败扣除积分", hint: "填 0 表示失败不扣分", type: "number", min: 0 },
      { path: "steal_settings.failure_cost_to_victim", label: "失败扣除转给被偷者", hint: "关闭后扣除积分直接从系统移除", type: "boolean" },
    ] },
    { id: "lottery", label: "抽奖规则", icon: "dices", description: "控制个人抽奖和群体抽奖的入口与成本。", fields: [
      { path: "lottery_settings.enabled", label: "启用抽奖", hint: "抽奖功能总开关", type: "boolean" },
      { path: "lottery_settings.default_mode", label: "默认抽奖模式", hint: "用户未指定模式时采用", type: "select", options: [["personal", "个人抽奖"], ["group", "群体抽奖"]] },
      { path: "lottery_settings.personal_enabled", label: "启用个人抽奖", hint: "允许用户独立开奖", type: "boolean" },
      { path: "lottery_settings.personal_cost", label: "个人抽奖消耗", hint: "每次个人抽奖扣除积分", type: "number", min: 1 },
      { path: "lottery_settings.personal_daily_limit", label: "个人每日次数", hint: "每位用户每天最多抽奖次数", type: "number", min: 1 },
      { path: "lottery_settings.group_enabled", label: "启用群体抽奖", hint: "允许多人组池开奖", type: "boolean" },
      { path: "lottery_settings.group_cost", label: "群体抽奖消耗", hint: "每人加入群体抽奖的积分", type: "number", min: 1 },
      { path: "lottery_settings.group_daily_limit_per_user", label: "群体每日次数", hint: "每位用户每天最多参与次数", type: "number", min: 1 },
      { path: "lottery_settings.group_required_participants", label: "开奖所需人数", hint: "达到人数后自动开奖", type: "number", min: 1 },
    ] },
    { id: "birthday", label: "生日功能", icon: "cake-slice", description: "管理生日签到奖励和寿星播报。", fields: [
      { path: "birthday_settings.enabled", label: "启用生日功能", hint: "开放生日记录与生日签到", type: "boolean" },
      { path: "birthday_settings.reward_points", label: "生日签到奖励", hint: "生日当天签到的额外积分", type: "number", min: 0 },
      { path: "birthday_settings.auto_record_when_unset", label: "自动记录生日", hint: "未设置生日时将首次生日签到日记为生日", type: "boolean" },
      { path: "birthday_settings.auto_broadcast_enabled", label: "寿星自动播报", hint: "每天定时播报当天寿星", type: "boolean" },
      { path: "birthday_settings.auto_broadcast_time", label: "播报时间", hint: "24 小时制，例如 08:00", type: "time" },
    ] },
    { id: "redpacket", label: "红包与榜单", icon: "gift", description: "控制积分红包额度和排行榜展示。", fields: [
      { path: "red_packet_settings.enabled", label: "启用积分红包", hint: "允许管理员创建积分红包", type: "boolean" },
      { path: "red_packet_settings.max_total_points", label: "单个红包积分上限", hint: "限制单次红包发放总额", type: "number", min: 1 },
      { path: "red_packet_settings.max_count", label: "单个红包份数上限", hint: "限制单次红包最大份数", type: "number", min: 1 },
      { path: "red_packet_settings.expire_minutes", label: "红包有效时间", hint: "分钟；填 0 表示不过期", type: "number", min: 0 },
      { path: "leaderboard_settings.display_limit", label: "积分榜人数", hint: "指令中展示的排行榜人数", type: "number", min: 1, max: 50 },
      { path: "leaderboard_settings.show_self_rank", label: "显示本人排名", hint: "榜单下方追加查询者自己的名次", type: "boolean" },
    ] },
    { id: "groupExchange", label: "群功能兑换", icon: "shield-check", description: "配置头衔、设精和禁言等群功能兑换。", fields: [
      { path: "exchange_settings.title_enabled", label: "开放头衔兑换", hint: "允许成员用积分兑换群头衔", type: "boolean" },
      { path: "exchange_settings.title_cost", label: "头衔兑换消耗", hint: "每次兑换群头衔扣除积分", type: "number", min: 1 },
      { path: "exchange_settings.title_max_length", label: "头衔最大长度", hint: "避免平台接口拒绝过长头衔", type: "number", min: 1 },
      { path: "exchange_settings.essence_enabled", label: "开放设精兑换", hint: "允许成员将回复消息设为精华", type: "boolean" },
      { path: "exchange_settings.essence_cost", label: "设精兑换消耗", hint: "每次设置精华扣除积分", type: "number", min: 1 },
      { path: "exchange_settings.mute_enabled", label: "开放禁言兑换", hint: "允许成员兑换禁言效果", type: "boolean" },
      { path: "exchange_settings.mute_cost", label: "禁言兑换消耗", hint: "每次兑换禁言扣除积分", type: "number", min: 1 },
      { path: "exchange_settings.mute_duration_seconds", label: "禁言时长", hint: "成功兑换后的禁言秒数", type: "number", min: 1 },
      { path: "exchange_settings.allow_mute_others", label: "允许禁言他人", hint: "关闭时只能兑换禁言自己", type: "boolean" },
    ] },
    { id: "operations", label: "运维与管理", icon: "settings-2", description: "配置管理员权限、备份与负积分提示。", fields: [
      { path: "admin_settings.log_operations", label: "记录管理操作", hint: "在日志中记录管理员加减积分", type: "boolean" },
      { path: "admin_settings.max_admin_give", label: "单次管理积分上限", hint: "限制管理员每次加减积分额度", type: "number", min: 1 },
      { path: "admin_settings.points_admin_ids", label: "积分管理员 QQ", hint: "一行一个 QQ 号", type: "list", full: true },
      { path: "backup_settings.enabled", label: "启用自动备份", hint: "每天按设定时间备份积分数据", type: "boolean" },
      { path: "backup_settings.auto_backup_time", label: "自动备份时间", hint: "24 小时制，例如 03:00", type: "time" },
      { path: "backup_settings.backup_paths", label: "备份路径", hint: "一行一个目录或文件路径", type: "list", full: true },
      { path: "negative_settings.debt_message", label: "负积分提示", hint: "负分用户尝试抽奖时显示", type: "textarea", full: true },
    ] },
  ];

  function icons() { if (window.lucide?.createIcons) window.lucide.createIcons({ attrs: { "aria-hidden": "true" } }); }

  function getBridge() {
    if (window.AstrBotPluginPage) return window.AstrBotPluginPage;
    try { if (window.parent && window.parent !== window) return window.parent.AstrBotPluginPage || null; } catch (_) { return null; }
    return null;
  }

  async function bridge() {
    for (let index = 0; index < 60; index += 1) {
      const api = getBridge();
      if (api?.apiGet && api?.apiPost) {
        if (typeof api.ready === "function") await api.ready();
        return api;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    throw new Error("请从 AstrBot 插件拓展页打开此页面");
  }

  async function requestEndpoint(method, path, body = {}) {
    const api = await bridge();
    // Normalize paths here because callers may pass either "overview" or
    // "/overview", and keep query parameters separate for apiGet.
    const url = new URL(String(path || ""), "https://astrbot-plugin-page.local/");
    const endpoint = `page/${url.pathname.replace(/^\/+/, "")}`.replace(/\/{2,}/g, "/");
    const params = Object.fromEntries(url.searchParams.entries());
    const result = method === "GET"
      ? await api.apiGet(endpoint, Object.keys(params).length ? params : undefined)
      : await api.apiPost(endpoint, body);
    if (result?.status === "error") {
      const error = new Error(result.message || "请求失败");
      error.code = result?.data?.error || result?.error || "";
      error.data = result?.data || {};
      throw error;
    }
    return result?.data ?? result;
  }

  async function uploadEndpoint(path, file) {
    const api = await bridge();
    const url = new URL(String(path || ""), "https://astrbot-plugin-page.local/");
    const endpoint = `page/${url.pathname.replace(/^\/+/, "")}`.replace(/\/{2,}/g, "/");
    const result = await api.upload(endpoint, file);
    if (result?.status === "error") {
      const error = new Error(result.message || "上传失败");
      error.code = result?.data?.error || result?.error || "";
      error.data = result?.data || {};
      throw error;
    }
    return result?.data ?? result;
  }

  function toast(message, kind = "success") {
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.innerHTML = `<i data-lucide="${kind === "error" ? "circle-alert" : "circle-check"}"></i><span>${escapeHtml(message)}</span>`;
    $("#toastRegion").append(node);
    icons();
    window.setTimeout(() => node.remove(), 3200);
  }

  function setConnection(kind, text) {
    const node = $("#connectionState");
    node.className = `connection-state ${kind}`;
    node.innerHTML = `<i data-lucide="${kind === "ok" ? "cloud-check" : kind === "error" ? "cloud-off" : "loader-circle"}"></i>${escapeHtml(text)}`;
    icons();
  }

  function setSaveStatus(status) {
    window.clearTimeout(saveStateTimer);
    state.saveStatus = status;
    state.saving = status === "saving";
    const states = {
      clean: { icon: "save", button: "保存修改", state: "", hidden: true },
      dirty: { icon: "save", button: "保存修改", state: "有未保存修改", hidden: false },
      saving: { icon: "loader-circle", button: "保存中", state: "正在保存", hidden: false },
      saved: { icon: "circle-check", button: "已保存", state: "修改已生效", hidden: false },
    };
    const current = states[status] || states.clean;
    const indicator = $("#saveState");
    indicator.hidden = current.hidden;
    indicator.className = `save-state ${status}`;
    indicator.innerHTML = `<i data-lucide="${status === "dirty" ? "circle-dot" : current.icon}"></i><span id="saveStateText">${current.state}</span>`;
    const button = $("#saveButton");
    button.innerHTML = `<i data-lucide="${current.icon}"></i><span id="saveButtonText">${current.button}</span>`;
    button.disabled = status !== "dirty" || !state.data?.can_save;
    icons();
    if (status === "saved") {
      saveStateTimer = window.setTimeout(() => {
        if (!state.dirty && state.saveStatus === "saved") setSaveStatus("clean");
      }, 2200);
    }
  }

  function setDirty(value = true, status = value ? "dirty" : "clean") {
    state.dirty = value;
    setSaveStatus(status);
  }

  function applyTheme(theme) {
    state.theme = theme;
    const dark = theme === "dark" || (theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    try { window.localStorage?.setItem("point-exchange-theme", theme); } catch (_) { /* sandboxed pages may not expose storage */ }
  }

  function toggleTheme() {
    const currentDark = document.documentElement.dataset.theme === "dark";
    applyTheme(currentDark ? "light" : "dark");
  }

  function stockFor(item) {
    if (item.repeatable) return Infinity;
    const used = Number(item.used_count || 0);
    return Math.max((item.contents || []).length - used, 0);
  }

  function formatNumber(value, compact = false) {
    const number = Number(value || 0);
    return (compact && Math.abs(number) >= 10000 ? compactNumber : numberFormat).format(number);
  }

  function getPath(source, path) {
    return String(path).split(".").reduce((current, key) => current?.[key], source);
  }

  function setPath(target, path, value) {
    const keys = String(path).split(".");
    const last = keys.pop();
    const parent = keys.reduce((current, key) => {
      if (!current[key] || typeof current[key] !== "object") current[key] = {};
      return current[key];
    }, target);
    parent[last] = value;
  }

  function updateMetrics() {
    const metrics = state.data?.metrics || {};
    $("#stockMetric").textContent = metrics.repeatable_count ? "∞" : (metrics.stock ?? 0);
    $("#itemMetric").textContent = `${metrics.enabled_count ?? 0} / ${metrics.item_count ?? 0}`;
    $("#redeemedMetric").textContent = metrics.redeemed_count ?? 0;
    $("#spentMetric").textContent = `${metrics.points_spent ?? 0} ${state.data?.points_name || "积分"}`;
    $("#recordCount").textContent = metrics.redeemed_count ?? 0;
    renderOverview();
  }

  function renderOverview() {
    const dashboard = state.data?.dashboard || {};
    const summary = dashboard.summary || {};
    const today = dashboard.today || {};
    const economy = dashboard.economy || {};
    const dashboardScope = dashboard.scope || { group_id: "", label: "全部群聊" };
    state.groupId = String(dashboardScope.group_id || "");
    state.historyRange = dashboard.point_history?.range || state.historyRange;
    const pointsName = state.data?.points_name || "积分";
    $("#totalPointsMetric").textContent = formatNumber(summary.total_points, true);
    $("#totalPointsMeta").textContent = `正积分 ${formatNumber(summary.positive_points, true)} · 净流通余额`;
    $("#userCountMetric").textContent = formatNumber(summary.user_count);
    $("#userCountMeta").textContent = `${formatNumber(summary.active_balance_users)} 人持有非零余额`;
    $("#averagePointsMetric").textContent = formatNumber(summary.average_points);
    $("#averagePointsMeta").textContent = state.groupId ? `${dashboardScope.label}成员均值` : `覆盖 ${formatNumber(summary.group_count)} 个群`;
    $("#debtPointsMetric").textContent = formatNumber(summary.debt_points, true);
    const debtUsers = (dashboard.distribution || []).find((item) => item.label === "负积分")?.count || 0;
    $("#debtPointsMeta").textContent = `${formatNumber(debtUsers)} 人处于负积分`;
    $("#todaySignIns").textContent = formatNumber(today.sign_ins);
    $("#todayActivityUsers").textContent = formatNumber(today.activity_users);
    $("#todayLotteryDraws").textContent = formatNumber(today.lottery_draws);
    $("#todayRedemptions").textContent = formatNumber(today.redemptions);
    $("#overviewUpdatedAt").textContent = `更新于 ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date())}`;

    const units = ["activityPointsUnit", "lotteryWonUnit", "lotterySpentUnit", "exchangeSpentUnit"];
    units.forEach((id) => { $(`#${id}`).textContent = pointsName; });
    $("#totalSignInDays").textContent = formatNumber(economy.total_sign_in_days, true);
    $("#activityPointsTotal").textContent = formatNumber(economy.activity_points, true);
    $("#lotteryWonTotal").textContent = formatNumber(economy.lottery_points_won, true);
    $("#lotterySpentTotal").textContent = formatNumber(economy.lottery_points_spent, true);
    $("#exchangeSpentTotal").textContent = formatNumber(economy.exchange_points_spent, true);
    $("#groupFilterLabel").textContent = dashboardScope.label || "全部群聊";
    $("#historyScopeLabel").textContent = dashboardScope.label || "全部群聊";
    $$('[data-history-range]').forEach((button) => button.classList.toggle("active", button.dataset.historyRange === state.historyRange));
    renderGroupFilterOptions();
    renderHistory(dashboard.point_history || {}, pointsName);
    renderTrend(dashboard.daily || []);
    renderDistribution(dashboard.distribution || [], summary.user_count || 0);
    renderLeaderboard(dashboard.leaderboard || [], pointsName);
    renderGroups(dashboard.groups || [], summary.group_count || 0, pointsName);
  }

  function renderGroupFilterOptions() {
    const dashboard = state.data?.dashboard || {};
    const query = state.groupQuery.trim().toLocaleLowerCase();
    const options = (dashboard.group_options || []).filter((item) => !query || String(item.group_id || "").toLocaleLowerCase().includes(query));
    const allSelected = !state.groupId;
    const rows = [`<button class="group-option${allSelected ? " selected" : ""}" type="button" role="option" aria-selected="${allSelected}" data-group-id=""><span><i data-lucide="message-square-more"></i><b>全部群聊</b></span><small>${formatNumber(dashboard.group_options?.length || 0)} 个群</small></button>`];
    rows.push(...options.map((item) => {
      const selected = String(item.group_id) === state.groupId;
      return `<button class="group-option${selected ? " selected" : ""}" type="button" role="option" aria-selected="${selected}" data-group-id="${escapeHtml(item.group_id)}"><span><i data-lucide="messages-square"></i><b>群 ${escapeHtml(item.group_id)}</b></span><small>${formatNumber(item.tracked_count)} 位用户 · ${formatNumber(item.total_points, true)} 积分</small></button>`;
    }));
    if (query && !options.length) rows.push('<div class="group-option-empty">没有匹配的群号</div>');
    $("#groupFilterOptions").innerHTML = rows.join("");
    icons();
  }

  function formatHistoryTime(value, range) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value || "");
    const options = range === "24h"
      ? { hour: "2-digit", minute: "2-digit", hour12: false }
      : { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false };
    return new Intl.DateTimeFormat("zh-CN", options).format(date);
  }

  function renderHistory(history, pointsName) {
    const chart = $("#historyChart");
    const points = Array.isArray(history.points) ? history.points : [];
    const delta = Number(history.total_delta || 0);
    const deltaNode = $("#historyDelta");
    deltaNode.className = `history-delta ${delta > 0 ? "positive" : delta < 0 ? "negative" : "neutral"}`;
    deltaNode.textContent = points.length > 1 ? `范围内 ${delta > 0 ? "+" : ""}${formatNumber(delta)} ${pointsName}` : "等待更多快照";
    $("#historyStart").textContent = history.history_started_at
      ? `真实历史始于 ${formatHistoryTime(history.history_started_at, "7d")}`
      : "真实历史将在首次采集后显示";
    $("#historyLatest").textContent = points.length ? `最新 ${formatNumber(points.at(-1).total_points, true)} ${pointsName}` : "—";

    if (!points.length) {
      chart.innerHTML = '<div class="history-empty"><i data-lucide="chart-no-axes-combined"></i><strong>还没有历史快照</strong><span>插件重新加载并产生积分变更后开始记录</span></div>';
      icons();
      return;
    }
    if (points.length === 1) {
      chart.innerHTML = `<div class="history-empty first-snapshot"><i data-lucide="circle-dot"></i><strong>已记录首个快照：${formatNumber(points[0].total_points, true)} ${escapeHtml(pointsName)}</strong><span>至少两个时间点后显示变化折线</span></div>`;
      icons();
      return;
    }

    const width = 800;
    const height = 240;
    const padding = { top: 18, right: 18, bottom: 30, left: 58 };
    const values = points.map((point) => Number(point.total_points || 0));
    let minimum = Math.min(...values);
    let maximum = Math.max(...values);
    const spread = Math.max(maximum - minimum, 1);
    minimum -= spread * 0.12;
    maximum += spread * 0.12;
    const innerWidth = width - padding.left - padding.right;
    const innerHeight = height - padding.top - padding.bottom;
    const x = (index) => padding.left + index / (points.length - 1) * innerWidth;
    const y = (value) => padding.top + (maximum - value) / (maximum - minimum) * innerHeight;
    const coordinates = points.map((point, index) => [x(index), y(Number(point.total_points || 0))]);
    const linePath = coordinates.map(([px, py], index) => `${index ? "L" : "M"}${px.toFixed(2)},${py.toFixed(2)}`).join(" ");
    const areaPath = `${linePath} L${coordinates.at(-1)[0].toFixed(2)},${(height - padding.bottom).toFixed(2)} L${coordinates[0][0].toFixed(2)},${(height - padding.bottom).toFixed(2)} Z`;
    const grid = [0, 1, 2, 3].map((step) => {
      const ratio = step / 3;
      const py = padding.top + ratio * innerHeight;
      const value = maximum - ratio * (maximum - minimum);
      return `<line class="history-grid-line" x1="${padding.left}" y1="${py}" x2="${width - padding.right}" y2="${py}"></line><text class="history-axis-label" x="${padding.left - 8}" y="${py + 3}" text-anchor="end">${escapeHtml(formatNumber(Math.round(value), true))}</text>`;
    }).join("");
    const labelIndexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
    const labels = labelIndexes.map((index) => `<text class="history-axis-label" x="${x(index)}" y="${height - 8}" text-anchor="${index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}">${escapeHtml(formatHistoryTime(points[index].captured_at, history.range))}</text>`).join("");
    const dots = points.length <= 36 ? coordinates.map(([px, py], index) => `<circle class="history-point" cx="${px}" cy="${py}" r="3"><title>${escapeHtml(formatHistoryTime(points[index].captured_at, history.range))} · ${escapeHtml(formatNumber(points[index].total_points))} ${escapeHtml(pointsName)}</title></circle>`).join("") : `<circle class="history-point latest" cx="${coordinates.at(-1)[0]}" cy="${coordinates.at(-1)[1]}" r="4"></circle>`;
    chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(history.range || "7d")} 总积分变化"><g>${grid}</g><path class="history-area" d="${areaPath}"></path><path class="history-line" d="${linePath}"></path>${dots}${labels}</svg>`;
  }

  function renderTrend(items) {
    const chart = $("#trendChart");
    if (!items.length) {
      chart.innerHTML = '<div class="trend-empty">暂无趋势数据</div>';
      return;
    }
    const maxSignIns = Math.max(...items.map((item) => Number(item.sign_ins || 0)), 1);
    const maxSpent = Math.max(...items.map((item) => Number(item.points_spent || 0)), 1);
    chart.innerHTML = items.map((item) => {
      const date = new Date(`${item.date}T00:00:00`);
      const label = Number.isNaN(date.getTime()) ? item.date : `${date.getMonth() + 1}/${date.getDate()}`;
      const signHeight = Math.max(Number(item.sign_ins || 0) / maxSignIns * 100, item.sign_ins ? 4 : 1);
      const spentHeight = Math.max(Number(item.points_spent || 0) / maxSpent * 100, item.points_spent ? 4 : 1);
      return `<div class="trend-day"><div class="trend-bars"><i class="trend-bar sign-ins" style="height:${signHeight}%" title="${escapeHtml(item.sign_ins || 0)} 人最近签到"></i><i class="trend-bar spent" style="height:${spentHeight}%" title="兑换消耗 ${escapeHtml(item.points_spent || 0)}"></i></div><small>${escapeHtml(label)}</small></div>`;
    }).join("");
  }

  function renderDistribution(items, total) {
    $("#distributionTotal").textContent = `${formatNumber(total)} 人`;
    const maximum = Math.max(...items.map((item) => Number(item.count || 0)), 1);
    $("#distributionChart").innerHTML = items.map((item) => `<div class="distribution-row"><span>${escapeHtml(item.label)}</span><div class="distribution-track"><i class="distribution-fill ${escapeHtml(item.tone || "muted")}" style="width:${Math.max(Number(item.count || 0) / maximum * 100, item.count ? 2 : 0)}%"></i></div><b>${formatNumber(item.count)}</b></div>`).join("");
  }

  function renderLeaderboard(items, pointsName) {
    const list = $("#leaderboardList");
    if (!items.length) { list.innerHTML = '<div class="overview-empty">暂无积分用户</div>'; return; }
    list.innerHTML = items.map((item, index) => `<div class="leader-row"><span class="leader-rank">${index + 1}</span><span class="leader-person"><strong>${escapeHtml(item.display_name || item.user_id)}</strong><small>${escapeHtml(item.user_id)} · 连签 ${escapeHtml(item.streak || 0)} 天</small></span><b class="leader-points">${formatNumber(item.points, true)} <small>${escapeHtml(pointsName)}</small></b></div>`).join("");
  }

  function renderGroups(items, count, pointsName) {
    $("#groupCountLabel").textContent = `${formatNumber(count)} 个群`;
    const list = $("#groupList");
    if (!items.length) { list.innerHTML = '<div class="overview-empty">暂无群成员数据</div>'; return; }
    const maximum = Math.max(...items.map((item) => Number(item.total_points || 0)), 1);
    list.innerHTML = items.slice(0, 8).map((item) => `<div class="group-row"><strong>群 ${escapeHtml(item.group_id)}</strong><span>${formatNumber(item.total_points, true)} ${escapeHtml(pointsName)}</span><small>${formatNumber(item.tracked_count)} 位积分用户 · 人均 ${formatNumber(item.average_points)}</small><div class="group-progress"><i style="width:${Math.max(Number(item.total_points || 0) / maximum * 100, item.total_points ? 2 : 0)}%"></i></div></div>`).join("");
  }

  function normalizeScope(value) {
    const mode = value?.mode === "whitelist" ? "whitelist" : "blacklist";
    const scope = uniqueScopeLines(Array.isArray(value?.scope) ? value.scope.join("\n") : "");
    return { mode, scope };
  }

  function updateScopeStatus() {
    const count = state.scope.scope.length;
    const whitelist = state.scope.mode === "whitelist";
    $("#scopeSummary").textContent = whitelist
      ? (count ? `白名单已开放 ${count} 个范围` : "白名单为空，当前未开放兑换")
      : (count ? `黑名单已排除 ${count} 个范围` : "黑名单为空，所有群和账号可兑换");
    const hint = $("#scopeHint");
    hint.textContent = whitelist
      ? "只有名单中的群或账号可以兑换；可填写 group: / user: 前缀"
      : "名单中的群或账号无法兑换；留空表示不限制";
    hint.classList.toggle("warning", whitelist && count === 0);
  }

  function renderScope() {
    $$('[data-scope-mode]').forEach((button) => {
      const active = button.dataset.scopeMode === state.scope.mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    $("#scopeValues").value = state.scope.scope.join("\n");
    updateScopeStatus();
  }

  function renderItemList() {
    const query = $("#itemSearch").value.trim().toLocaleLowerCase();
    const visible = state.draft.map((item, index) => ({ item, index })).filter(({ item }) => !query || String(item.name || "").toLocaleLowerCase().includes(query));
    const list = $("#itemList");
    if (!visible.length) {
      list.innerHTML = state.draft.length
        ? `<div class="list-empty"><i data-lucide="search-x"></i><span>没有匹配的兑换物</span><button class="text-button" type="button" data-action="clear-search">清除搜索</button></div>`
        : `<div class="list-empty"><i data-lucide="package-open"></i><span>还没有兑换物</span><small>从右侧快速向导开始创建</small></div>`;
      icons();
      return;
    }
    list.innerHTML = visible.map(({ item, index }) => {
      const stock = stockFor(item);
      const stockLabel = item.repeatable ? "∞" : stock;
      return `<button class="item-row ${index === state.selected ? "active" : ""} ${item.enabled ? "" : "disabled"}" type="button" data-index="${index}">
        <strong>${escapeHtml(item.name || "未命名兑换物")}</strong><span class="stock-pill ${item.repeatable || stock ? "" : "empty"}">${stockLabel}</span>
        <small>${escapeHtml(item.cost)} ${escapeHtml(state.data?.points_name || "积分")}${item.content_type === "image" ? " · 图片" : item.content_type === "video" ? " · 视频" : " · 文本"}${item.selection_mode === "random" ? " · 随机" : " · 按序"}${item.repeatable ? " · 可重复" : ""}${item.private_only ? " · 结果私聊" : ""}</small>
      </button>`;
    }).join("");
  }

  function renderEditor() {
    const item = state.draft[state.selected];
    $("#editorEmpty").hidden = Boolean(item);
    $("#itemEditor").hidden = !item;
    if (!item) { icons(); return; }
    $("#editorTitle").textContent = item.name || "未命名兑换物";
    $("#itemName").value = item.name || "";
    $("#itemCost").value = item.cost || 1;
    $("#itemEnabled").checked = Boolean(item.enabled);
    $("#itemPrivate").checked = Boolean(item.private_only);
    $("#itemContentType").value = ["text", "image", "video"].includes(item.content_type) ? item.content_type : "text";
    $("#itemRepeatable").checked = Boolean(item.repeatable);
    $("#itemSelectionMode").value = item.selection_mode === "random" ? "random" : "sequential";
    $("#itemContents").value = (item.contents || []).join("\n");
    $("#successTemplate").value = item.success_template || DEFAULT_TEMPLATE;
    $("#pointsNameSuffix").textContent = state.data?.points_name || "积分";
    clearValidation();
    updateStockEditor();
    updateContentEditor();
    renderTemplatePreview();
    icons();
  }

  function updateStockEditor(sourceText = null) {
    const item = state.draft[state.selected];
    if (!item) return;
    const total = (item.contents || []).length;
    const used = Math.min(Number(item.used_count || 0), total);
    const available = item.repeatable ? Infinity : Math.max(total - used, 0);
    $("#availableStock").textContent = item.repeatable ? "∞" : available;
    $("#usedStock").textContent = used;
    $("#totalStock").textContent = total;
    const meta = $("#contentMeta");
    const entered = sourceText === null ? total : String(sourceText).split(/\r?\n/).map((line) => line.trim()).filter(Boolean).length;
    const ignored = Math.max(entered - total, 0);
    meta.textContent = total
      ? `${item.repeatable ? "不限" : available} 份可用，共识别 ${total} 份${ignored ? `，忽略 ${ignored} 条重复内容` : ""}`
      : "还没有可发放内容，保存后用户暂时无法兑换";
    meta.classList.toggle("warning", !item.repeatable && available === 0);
  }

  function renderTemplatePreview() {
    const item = state.draft[state.selected];
    if (!item) return;
    const pointsName = state.data?.points_name || "积分";
    const used = Math.min(Number(item.used_count || 0), (item.contents || []).length);
    const sampleContent = item.contents?.[used] || item.contents?.[0] || "示例发放内容";
    let template = item.success_template || DEFAULT_TEMPLATE;
    if (!template.includes("{content}")) template += "\n兑换内容：{content}";
    const values = {
      item: item.name || "兑换物名称",
      content: sampleContent,
      cost: item.cost || 1,
      points_name: pointsName,
      remaining: 500,
    };
    $("#templatePreview").textContent = template.replace(
      /\{(item|content|cost|points_name|remaining)\}/g,
      (_, key) => String(values[key]),
    );
  }

  function selectItem(index, reveal = false) {
    state.selected = Number(index);
    renderItemList();
    renderEditor();
    if (reveal && window.matchMedia?.("(max-width: 680px)").matches) {
      window.requestAnimationFrame(() => $(".editor-surface").scrollIntoView({ behavior: "smooth", block: "start" }));
    }
  }

  function nextItemName() {
    const names = new Set(state.draft.map((item) => item.name));
    let index = 1;
    while (names.has(index === 1 ? "新兑换物" : `新兑换物 ${index}`)) index += 1;
    return index === 1 ? "新兑换物" : `新兑换物 ${index}`;
  }

  function addItem(example = false) {
    const item = { name: nextItemName(), enabled: true, cost: 100, content_type: "text", contents: [], selection_mode: "sequential", repeatable: false, private_only: true, success_template: DEFAULT_TEMPLATE, stock: 0, used_count: 0, total_count: 0 };
    if (example) {
      item.name = state.draft.some((entry) => entry.name === "新人礼包示例") ? nextItemName() : "新人礼包示例";
      item.enabled = false;
      item.contents = ["奖励内容 001", "奖励内容 002", "奖励内容 003"];
    }
    state.draft.push(item);
    setDirty();
    selectItem(state.draft.length - 1, true);
    window.requestAnimationFrame(() => {
      $("#itemName").select();
      if (example) toast("已载入未启用的填写示例，请按实际内容修改");
    });
  }

  function clearValidation() {
    ["itemName", "itemCost"].forEach((name) => {
      const input = $(`#${name}`);
      const error = $(`#${name}Error`);
      input?.removeAttribute("aria-invalid");
      if (error) { error.hidden = true; error.textContent = ""; }
    });
  }

  function showValidation(index, field, message) {
    switchView("inventory");
    selectItem(index, true);
    const input = $(`#${field}`);
    const error = $(`#${field}Error`);
    input.setAttribute("aria-invalid", "true");
    error.textContent = message;
    error.hidden = false;
    window.requestAnimationFrame(() => input.focus());
    toast(message, "error");
  }

  function normalizeContents() {
    const input = $("#itemContents");
    const cleaned = uniqueLines(input.value).join("\n");
    const changed = cleaned !== input.value;
    input.value = cleaned;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    toast(changed ? "已移除空行和重复内容" : "内容已经整理好了");
  }

  function updateContentEditor() {
    const item = state.draft[state.selected];
    if (!item) return;
    const type = ["text", "image", "video"].includes(item.content_type) ? item.content_type : "text";
    const isMedia = type !== "text";
    $("#textContentEditor").hidden = isMedia;
    $("#mediaContentEditor").hidden = !isMedia;
    $("#contentUploadInput").accept = type === "image" ? "image/*" : "video/*";
    $("#uploadMediaButton").innerHTML = `<i data-lucide="upload"></i>上传${type === "image" ? "图片" : "视频"}`;
    const list = $("#mediaContentList");
    if (isMedia) {
      list.innerHTML = (item.contents || []).map((content, index) => {
        const source = String(content || "").replace(/^(image|video):/i, "");
        const filename = source.split(/[\\/]/).pop() || `媒体 ${index + 1}`;
        return `<div class="media-content-row"><span><i data-lucide="${type === "image" ? "image" : "video"}"></i><strong>${escapeHtml(filename)}</strong></span><button type="button" class="icon-button" data-remove-media="${index}" title="移除媒体" aria-label="移除媒体"><i data-lucide="x"></i></button></div>`;
      }).join("") || '<div class="media-empty">还没有上传媒体</div>';
    }
    icons();
  }

  async function uploadMedia(files) {
    const item = state.draft[state.selected];
    if (!item || !files.length) return;
    const button = $("#uploadMediaButton");
    const input = $("#contentUploadInput");
    button.disabled = true;
    button.classList.add("loading");
    try {
      const contents = [...(item.contents || [])];
      for (const file of files) {
        const uploaded = await uploadEndpoint("media/upload", file);
        if (uploaded?.kind !== item.content_type) {
          throw new Error("上传文件类型与当前奖励类型不一致");
        }
        if (uploaded?.content) contents.push(uploaded.content);
      }
      item.contents = uniqueLines(contents);
      setDirty();
      renderEditor();
      toast(`已上传 ${files.length} 个媒体文件`);
    } catch (error) {
      toast(error.message || "上传媒体失败", "error");
    } finally {
      button.disabled = false;
      button.classList.remove("loading");
      input.value = "";
    }
  }

  function deleteSelected() {
    const item = state.draft[state.selected];
    if (!item) return;
    $("#deleteMessage").textContent = `将删除“${item.name}”及配置中的全部库存内容，历史兑换记录仍会保留。`;
    $("#deleteDialog").showModal();
  }

  function confirmDelete() {
    if (state.selected < 0) return;
    state.draft.splice(state.selected, 1);
    state.selected = Math.min(state.selected, state.draft.length - 1);
    setDirty();
    renderItemList();
    renderEditor();
  }

  function updateSelected(key, value, rerenderList = true) {
    const item = state.draft[state.selected];
    if (!item) return;
    item[key] = value;
    setDirty();
    if (rerenderList) renderItemList();
  }

  function renderRecords() {
    const query = $("#recordSearch").value.trim().toLocaleLowerCase();
    const records = (state.data?.redemptions || []).filter((item) => !query || `${item.item_name} ${item.user_id}`.toLocaleLowerCase().includes(query));
    const list = $("#recordList");
    if (!records.length) {
      list.innerHTML = `<div class="record-empty">${query ? "没有匹配的兑换记录" : "暂无兑换记录"}</div>`;
      return;
    }
    list.innerHTML = records.map((item) => `<div class="record-table record-row">
      <div class="record-item"><strong>${escapeHtml(item.item_name || "已删除兑换物")}</strong><small class="record-status ${item.delivery_status === "uncertain" ? "uncertain" : ""}">${item.delivery_status === "uncertain" ? "待核对" : "已发放"}</small></div>
      <span>${escapeHtml(item.user_id || "未知")}</span>
      <span class="record-cost">-${escapeHtml(item.cost)} ${escapeHtml(state.data?.points_name || "积分")}</span>
      <span>${escapeHtml(formatDate(item.redeemed_at))}</span>
    </div>`).join("");
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
  }

  function switchPage(page) {
    state.page = ["overview", "exchange", "settings"].includes(page) ? page : "overview";
    $$(".workspace-tab").forEach((button) => {
      const active = button.dataset.page === state.page;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    $$('[data-page-panel]').forEach((panel) => {
      const active = panel.dataset.pagePanel === state.page;
      panel.hidden = !active;
      panel.classList.toggle("active", active);
    });
    $("#saveButton").hidden = state.page !== "exchange";
    if (state.page === "settings" && !$("#settingsSections").children.length) renderSettings();
    icons();
  }

  function renderSettings() {
    const nav = $("#settingsNav");
    const root = $("#settingsSections");
    nav.innerHTML = SETTINGS_SECTIONS.map((section, index) => `<button type="button" class="${index === 0 ? "active" : ""}" data-settings-target="${escapeHtml(section.id)}"><i data-lucide="${escapeHtml(section.icon)}"></i>${escapeHtml(section.label)}</button>`).join("");
    root.innerHTML = SETTINGS_SECTIONS.map((section) => `<section id="settings-${escapeHtml(section.id)}" class="settings-section" data-settings-section="${escapeHtml(section.id)}"><div class="settings-section-heading"><span class="settings-section-icon"><i data-lucide="${escapeHtml(section.icon)}"></i></span><div><h3>${escapeHtml(section.label)}</h3><p>${escapeHtml(section.description)}</p></div></div><div class="settings-fields">${section.fields.map(renderSettingControl).join("")}</div></section>`).join("");
    icons();
  }

  function renderSettingControl(field) {
    const value = getPath(state.settingsDraft, field.path);
    let control = "";
    if (field.type === "boolean") {
      control = `<label class="setting-switch"><input type="checkbox" data-setting-path="${escapeHtml(field.path)}" ${value ? "checked" : ""} aria-label="${escapeHtml(field.label)}" /></label>`;
    } else if (field.type === "select") {
      control = `<select data-setting-path="${escapeHtml(field.path)}">${field.options.map(([optionValue, label]) => `<option value="${escapeHtml(optionValue)}" ${String(value) === optionValue ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select>`;
    } else if (["textarea", "list"].includes(field.type)) {
      const display = field.type === "list" && Array.isArray(value) ? value.join("\n") : String(value ?? "");
      control = `<textarea data-setting-path="${escapeHtml(field.path)}" data-setting-type="${field.type}" rows="3">${escapeHtml(display)}</textarea>`;
    } else {
      const type = field.type === "time" ? "time" : field.type === "number" ? "number" : "text";
      const min = field.min !== undefined ? ` min="${field.min}"` : "";
      const max = field.max !== undefined ? ` max="${field.max}"` : "";
      const step = field.step !== undefined ? ` step="${field.step}"` : "";
      control = `<input type="${type}" data-setting-path="${escapeHtml(field.path)}" value="${escapeHtml(value ?? "")}"${min}${max}${step} />`;
    }
    return `<label class="setting-control ${field.full ? "full" : ""}"><span class="setting-copy"><b>${escapeHtml(field.label)}</b><small>${escapeHtml(field.hint)}</small></span><span class="setting-input">${control}</span></label>`;
  }

  function setSettingsDirty(value = true) {
    state.settingsDirty = value;
    $("#settingsDirtyLabel").hidden = !value;
    $("#saveSettingsButton").disabled = !value || !state.data?.can_save || state.settingsSaving;
  }

  function updateSettingFromInput(input) {
    const path = input.dataset.settingPath;
    if (!path) return;
    let value;
    if (input.type === "checkbox") value = input.checked;
    else if (input.type === "number") value = input.value === "" ? 0 : Number(input.value);
    else if (input.dataset.settingType === "list") value = uniqueLines(input.value);
    else value = input.value;
    setPath(state.settingsDraft, path, value);
    setSettingsDirty();
  }

  function switchView(view) {
    state.view = view;
    $$(".view-tab").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    $$('[data-view-panel]').forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== view; panel.classList.toggle("active", panel.dataset.viewPanel === view); });
    if (view === "records") renderRecords();
  }

  function setGroupFilterOpen(open) {
    const popover = $("#groupFilterPopover");
    const button = $("#groupFilterButton");
    popover.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    $("#groupFilter").classList.toggle("open", open);
    if (open) {
      state.groupQuery = "";
      $("#groupFilterSearch").value = "";
      renderGroupFilterOptions();
      window.setTimeout(() => $("#groupFilterSearch").focus(), 0);
    }
  }

  async function loadDashboard(groupId = state.groupId, historyRange = state.historyRange) {
    if (!state.data || state.dashboardLoading) return;
    const previousGroupId = String(state.data.dashboard?.scope?.group_id || "");
    const requestId = ++state.dashboardRequest;
    state.dashboardLoading = true;
    state.groupId = String(groupId || "");
    state.historyRange = historyRange;
    $("#overviewPage").classList.add("dashboard-loading");
    $("#groupFilterButton").disabled = true;
    $$('[data-history-range]').forEach((button) => { button.disabled = true; });
    try {
      const params = new URLSearchParams({ group_id: state.groupId, range: state.historyRange });
      const result = await requestEndpoint("GET", `dashboard?${params.toString()}`);
      if (requestId !== state.dashboardRequest) return;
      state.data.dashboard = result.dashboard || {};
      renderOverview();
      $("#overviewUpdatedAt").textContent = `更新于 ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date())}`;
    } catch (error) {
      state.groupId = previousGroupId;
      toast(error.message || "无法筛选群聊数据", "error");
      renderOverview();
    } finally {
      if (requestId === state.dashboardRequest) {
        state.dashboardLoading = false;
        $("#overviewPage").classList.remove("dashboard-loading");
        $("#groupFilterButton").disabled = false;
        $$('[data-history-range]').forEach((button) => { button.disabled = false; });
      }
    }
  }

  async function loadData(force = false) {
    if ((state.dirty || state.settingsDirty) && !force && !window.confirm("当前修改尚未保存，仍要刷新吗？")) return;
    setConnection("", "连接中");
    $("#refreshButton").disabled = true;
    try {
      const requestedGroupId = state.groupId;
      const requestedRange = state.historyRange;
      const data = await requestEndpoint("GET", "overview");
      if (requestedGroupId || requestedRange !== "7d") {
        const params = new URLSearchParams({ group_id: requestedGroupId, range: requestedRange });
        const scoped = await requestEndpoint("GET", `dashboard?${params.toString()}`);
        data.dashboard = scoped.dashboard || data.dashboard;
      }
      state.data = data;
      state.draft = JSON.parse(JSON.stringify(data.items || []));
      state.scope = normalizeScope(data.exchange_scope);
      state.settingsDraft = JSON.parse(JSON.stringify(data.settings || {}));
      state.selected = state.draft.length ? Math.min(Math.max(state.selected, 0), state.draft.length - 1) : -1;
      setDirty(false);
      setSettingsDirty(false);
      updateMetrics();
      renderScope();
      renderItemList();
      renderEditor();
      renderRecords();
      renderSettings();
      setConnection("ok", "已同步");
      if (!data.can_save) toast("当前 AstrBot 版本不支持从拓展页保存配置", "error");
    } catch (error) {
      setConnection("error", "连接失败");
      toast(error.message || "无法读取兑换数据", "error");
    } finally {
      $("#refreshButton").disabled = false;
      icons();
    }
  }

  async function saveData() {
    if (!state.dirty || !state.data?.can_save || state.saving) return;
    clearValidation();
    const names = new Set();
    for (let index = 0; index < state.draft.length; index += 1) {
      const item = state.draft[index];
      const name = String(item.name || "").trim();
      if (!name) { showValidation(index, "itemName", "请填写兑换物名称"); return; }
      if (names.has(name.toLocaleLowerCase())) { showValidation(index, "itemName", `名称“${name}”已经用过，请换一个`); return; }
      item.name = name;
      names.add(name.toLocaleLowerCase());
      if (!Number.isFinite(Number(item.cost)) || Number(item.cost) < 1 || Number(item.cost) > 1000000000) { showValidation(index, "itemCost", `请为“${name}”填写有效的兑换积分`); return; }
    }
    setSaveStatus("saving");
    setConnection("", "保存中");
    try {
      const pendingSettings = state.settingsDirty ? state.settingsDraft : null;
      const data = await requestEndpoint("POST", "items/save", { revision: state.data.revision, items: state.draft, exchange_scope: state.scope });
      state.data = data;
      state.draft = JSON.parse(JSON.stringify(data.items || []));
      state.scope = normalizeScope(data.exchange_scope);
      state.settingsDraft = pendingSettings || JSON.parse(JSON.stringify(data.settings || {}));
      state.selected = state.draft.length ? Math.min(Math.max(state.selected, 0), state.draft.length - 1) : -1;
      setDirty(false, "saved");
      updateMetrics();
      renderScope();
      renderItemList();
      renderEditor();
      renderRecords();
      if (!pendingSettings) renderSettings();
      setConnection("ok", "已保存");
      toast("兑换配置已保存并立即生效");
    } catch (error) {
      setConnection("error", "保存失败");
      toast(error.message || "保存失败", "error");
      setSaveStatus("dirty");
    }
  }

  async function saveSettings() {
    if (!state.settingsDirty || !state.data?.can_save || state.settingsSaving) return;
    state.settingsSaving = true;
    const button = $("#saveSettingsButton");
    button.disabled = true;
    button.innerHTML = '<i data-lucide="loader-circle"></i><span>保存中</span>';
    button.classList.add("loading");
    setConnection("", "保存中");
    icons();
    try {
      const pendingExchange = state.dirty;
      const pendingDraft = state.draft;
      const pendingScope = state.scope;
      const data = await requestEndpoint("POST", "settings/save", { revision: state.data.revision, settings: state.settingsDraft });
      state.data = data;
      state.draft = pendingExchange ? pendingDraft : JSON.parse(JSON.stringify(data.items || []));
      state.scope = pendingExchange ? pendingScope : normalizeScope(data.exchange_scope);
      state.settingsDraft = JSON.parse(JSON.stringify(data.settings || {}));
      setSettingsDirty(false);
      updateMetrics();
      if (!pendingExchange) {
        renderScope();
        renderItemList();
        renderEditor();
        renderRecords();
      }
      renderSettings();
      setConnection("ok", "已保存");
      toast("常用配置已保存并立即生效");
    } catch (error) {
      setConnection("error", "保存失败");
      toast(error.message || "保存配置失败", "error");
    } finally {
      state.settingsSaving = false;
      button.classList.remove("loading");
      button.innerHTML = '<i data-lucide="save"></i><span>保存配置</span>';
      button.disabled = !state.settingsDirty || !state.data?.can_save;
      icons();
    }
  }

  function bindEvents() {
    $("#themeButton").addEventListener("click", toggleTheme);
    $("#refreshButton").addEventListener("click", () => loadData());
    $("#groupFilterButton").addEventListener("click", () => setGroupFilterOpen($("#groupFilterPopover").hidden));
    $("#groupFilterSearch").addEventListener("input", (event) => { state.groupQuery = event.target.value; renderGroupFilterOptions(); });
    $("#groupFilterOptions").addEventListener("click", (event) => {
      const option = event.target.closest("[data-group-id]");
      if (!option) return;
      const groupId = option.dataset.groupId || "";
      setGroupFilterOpen(false);
      if (groupId !== state.groupId) loadDashboard(groupId, state.historyRange);
    });
    $$('[data-history-range]').forEach((button) => button.addEventListener("click", () => {
      if (button.dataset.historyRange !== state.historyRange) loadDashboard(state.groupId, button.dataset.historyRange);
    }));
    $("#saveButton").addEventListener("click", saveData);
    $("#saveSettingsButton").addEventListener("click", saveSettings);
    $$(".workspace-tab").forEach((button) => button.addEventListener("click", () => switchPage(button.dataset.page)));
    $("#settingsNav").addEventListener("click", (event) => {
      const button = event.target.closest("[data-settings-target]");
      if (!button) return;
      $$("#settingsNav button").forEach((item) => item.classList.toggle("active", item === button));
      $(`#settings-${button.dataset.settingsTarget}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("#settingsForm").addEventListener("input", (event) => updateSettingFromInput(event.target));
    $("#settingsForm").addEventListener("change", (event) => updateSettingFromInput(event.target));
    $("#addItemButton").addEventListener("click", () => addItem());
    $$("[data-action='add-item']").forEach((button) => button.addEventListener("click", () => addItem()));
    $("[data-action='add-example']").addEventListener("click", () => addItem(true));
    $("#deleteItemButton").addEventListener("click", deleteSelected);
    $("#confirmDelete").addEventListener("click", confirmDelete);
    $("#itemSearch").addEventListener("input", renderItemList);
    $("#recordSearch").addEventListener("input", renderRecords);
    $$('[data-scope-mode]').forEach((button) => button.addEventListener("click", () => {
      const mode = button.dataset.scopeMode === "whitelist" ? "whitelist" : "blacklist";
      if (state.scope.mode === mode) return;
      state.scope.mode = mode;
      setDirty();
      renderScope();
    }));
    $("#scopeValues").addEventListener("input", (event) => {
      const nextScope = uniqueScopeLines(event.target.value);
      if (JSON.stringify(nextScope) !== JSON.stringify(state.scope.scope)) {
        state.scope.scope = nextScope;
        setDirty();
      }
      updateScopeStatus();
    });
    $("#scopeValues").addEventListener("blur", (event) => { event.target.value = state.scope.scope.join("\n"); });
    $("#itemList").addEventListener("click", (event) => {
      const action = event.target.closest("[data-action]")?.dataset.action;
      if (action === "clear-search") { $("#itemSearch").value = ""; renderItemList(); $("#itemSearch").focus(); return; }
      const row = event.target.closest("[data-index]");
      if (row) selectItem(row.dataset.index, true);
    });
    $$(".view-tab").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
    $("#itemName").addEventListener("input", (event) => { clearValidation(); updateSelected("name", event.target.value); $("#editorTitle").textContent = event.target.value || "未命名兑换物"; renderTemplatePreview(); });
    $("#itemCost").addEventListener("input", (event) => { clearValidation(); updateSelected("cost", Number(event.target.value || 0)); renderTemplatePreview(); });
    $("#itemEnabled").addEventListener("change", (event) => updateSelected("enabled", event.target.checked));
    $("#itemPrivate").addEventListener("change", (event) => updateSelected("private_only", event.target.checked));
    $("#itemContentType").addEventListener("change", (event) => {
      const item = state.draft[state.selected];
      const nextType = event.target.value;
      if (item && item.contents?.length && nextType !== item.content_type) {
        const hasIncompatibleContent = nextType === "text"
          ? item.contents.some((content) => /^(image|video)\s*:/i.test(String(content)))
          : item.contents.some((content) => !new RegExp(`^${nextType}\\s*:`, "i").test(String(content)));
        if (hasIncompatibleContent && !window.confirm("当前内容与新奖励类型不一致，切换后清空现有内容吗？")) {
          event.target.value = item.content_type || "text";
          return;
        }
        if (hasIncompatibleContent) item.contents = [];
      }
      updateSelected("content_type", nextType, false);
      updateContentEditor();
      renderEditor();
    });
    $("#itemRepeatable").addEventListener("change", (event) => { updateSelected("repeatable", event.target.checked); updateStockEditor(); renderItemList(); });
    $("#itemSelectionMode").addEventListener("change", (event) => { updateSelected("selection_mode", event.target.value === "random" ? "random" : "sequential"); renderItemList(); });
    $("#itemContents").addEventListener("input", (event) => { updateSelected("contents", uniqueLines(event.target.value), false); updateStockEditor(event.target.value); renderTemplatePreview(); });
    $("#mediaContentList").addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove-media]");
      if (!button) return;
      const item = state.draft[state.selected];
      if (!item) return;
      item.contents.splice(Number(button.dataset.removeMedia), 1);
      setDirty();
      renderEditor();
    });
    $("#uploadMediaButton").addEventListener("click", () => $("#contentUploadInput").click());
    $("#contentUploadInput").addEventListener("change", (event) => uploadMedia([...event.target.files]));
    $("#cleanContentsButton").addEventListener("click", normalizeContents);
    $("#successTemplate").addEventListener("input", (event) => { updateSelected("success_template", event.target.value, false); renderTemplatePreview(); });
    $("#resetTemplateButton").addEventListener("click", () => { $("#successTemplate").value = DEFAULT_TEMPLATE; updateSelected("success_template", DEFAULT_TEMPLATE, false); renderTemplatePreview(); });
    $(".variable-row").addEventListener("click", (event) => {
      const button = event.target.closest("[data-variable]");
      if (!button) return;
      const input = $("#successTemplate");
      const start = input.selectionStart ?? input.value.length;
      input.setRangeText(button.dataset.variable, start, input.selectionEnd ?? start, "end");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
    document.addEventListener("click", (event) => { if (!event.target.closest("#groupFilter")) setGroupFilterOpen(false); });
    window.addEventListener("beforeunload", (event) => { if (state.dirty || state.settingsDirty) { event.preventDefault(); event.returnValue = ""; } });
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !$("#groupFilterPopover").hidden) { setGroupFilterOpen(false); $("#groupFilterButton").focus(); return; }
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "s") {
        event.preventDefault();
        if (state.page === "settings") saveSettings();
        else if (state.page === "exchange") saveData();
      }
    });
  }

  function init() {
    let savedTheme = "system";
    try { savedTheme = window.localStorage?.getItem("point-exchange-theme") || "system"; } catch (_) { /* noop */ }
    applyTheme(savedTheme);
    bindEvents();
    icons();
    switchPage("overview");
    loadData(true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true }); else init();
})();
