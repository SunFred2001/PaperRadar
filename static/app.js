const state = {
  papers: [],
  filterText: "",
  mode: "server",
};

const elements = {
  refreshButton: document.querySelector("#refreshButton"),
  pushTestButton: document.querySelector("#pushTestButton"),
  statusBadge: document.querySelector("#statusBadge"),
  paperCount: document.querySelector("#paperCount"),
  lastSuccess: document.querySelector("#lastSuccess"),
  nextRefresh: document.querySelector("#nextRefresh"),
  nextPush: document.querySelector("#nextPush"),
  configForm: document.querySelector("#configForm"),
  searchQuery: document.querySelector("#searchQuery"),
  maxResults: document.querySelector("#maxResults"),
  lookbackDays: document.querySelector("#lookbackDays"),
  refreshInterval: document.querySelector("#refreshInterval"),
  contactEmail: document.querySelector("#contactEmail"),
  pushEnabled: document.querySelector("#pushEnabled"),
  pushChannel: document.querySelector("#pushChannel"),
  pushTime: document.querySelector("#pushTime"),
  pushTimezone: document.querySelector("#pushTimezone"),
  pushMaxPapers: document.querySelector("#pushMaxPapers"),
  serverchanSendkey: document.querySelector("#serverchanSendkey"),
  wecomWebhookUrl: document.querySelector("#wecomWebhookUrl"),
  pushMessage: document.querySelector("#pushMessage"),
  pushMeta: document.querySelector("#pushMeta"),
  keywordFilter: document.querySelector("#keywordFilter"),
  feedMessage: document.querySelector("#feedMessage"),
  papersGrid: document.querySelector("#papersGrid"),
  paperCardTemplate: document.querySelector("#paperCardTemplate"),
  channelFields: Array.from(document.querySelectorAll(".channel-field")),
};

function resolvePath(relativePath) {
  return new URL(relativePath, window.location.href).toString();
}

function formatDate(value) {
  if (!value) {
    return "尚未记录";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function renderStatus(status, message) {
  elements.statusBadge.textContent = message || status || "未知状态";
  elements.statusBadge.className = "status-pill";
  if (status) {
    elements.statusBadge.classList.add(`status-${status}`);
  }
}

function setBusy(isBusy) {
  elements.refreshButton.disabled = isBusy;
  elements.pushTestButton.disabled = isBusy;
  elements.configForm.querySelector("button[type='submit']").disabled = isBusy;
}

function setStaticMode() {
  state.mode = "static";
  elements.refreshButton.disabled = true;
  elements.pushTestButton.disabled = true;
  Array.from(elements.configForm.elements).forEach((field) => {
    if (field.id !== "keywordFilter") {
      field.disabled = true;
    }
  });
  elements.feedMessage.textContent = "当前为在线只读模式。论文列表会由 GitHub Actions 每日自动更新，配置修改请在仓库或本地版本中完成。";
  elements.pushMeta.textContent = "在线站点不提供密钥录入或测试推送，微信推送由仓库 Secrets 和定时任务负责。";
}

function syncPushChannelFields() {
  const channel = elements.pushChannel.value;
  elements.channelFields.forEach((field) => {
    const isActive = field.dataset.channel === channel;
    field.classList.toggle("is-hidden", !isActive);
    field.querySelector("input").disabled = !isActive;
  });
}

function fillConfig(config) {
  elements.searchQuery.value = config.search_query || "";
  elements.maxResults.value = config.max_results || 15;
  elements.lookbackDays.value = config.lookback_days || 90;
  elements.refreshInterval.value = config.refresh_interval_hours || 24;
  elements.contactEmail.value = config.contact_email || "";
  elements.pushEnabled.checked = Boolean(config.push_enabled);
  elements.pushChannel.value = config.push_channel || "serverchan";
  elements.pushTime.value = config.push_time || "09:00";
  elements.pushTimezone.value = config.push_timezone || "Asia/Shanghai";
  elements.pushMaxPapers.value = config.push_max_papers || 5;
  elements.serverchanSendkey.value = config.serverchan_sendkey || "";
  elements.wecomWebhookUrl.value = config.wecom_webhook_url || "";
  syncPushChannelFields();
}

function matchesFilter(paper, filterText) {
  if (!filterText) {
    return true;
  }
  const haystack = [
    paper.title,
    paper.abstract,
    paper.journal,
    ...(paper.authors || []),
    ...(paper.publication_types || []),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(filterText.toLowerCase());
}

function createChip(text, className = "tag") {
  const chip = document.createElement("span");
  chip.className = className;
  chip.textContent = text;
  return chip;
}

function createLink(href, text) {
  const link = document.createElement("a");
  link.className = "link-chip";
  link.href = href;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = text;
  return link;
}

function renderPapers() {
  elements.papersGrid.innerHTML = "";
  const filtered = state.papers.filter((paper) => matchesFilter(paper, state.filterText));

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = state.papers.length
      ? "当前关键词没有命中缓存结果，可以换个词试试。"
      : "还没有可展示的论文，先点一次“立即刷新”即可开始。";
    elements.papersGrid.append(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  filtered.forEach((paper) => {
    const node = elements.paperCardTemplate.content.cloneNode(true);
    const title = node.querySelector(".paper-title");
    const titleLink = document.createElement("a");
    titleLink.href = paper.pubmed_url || "#";
    titleLink.target = "_blank";
    titleLink.rel = "noreferrer";
    titleLink.textContent = paper.title || "Untitled";
    title.append(titleLink);

    node.querySelector(".paper-date").textContent = paper.published_on || "日期未标注";
    node.querySelector(".paper-journal").textContent = paper.journal || "期刊信息缺失";
    node.querySelector(".paper-authors").textContent = (paper.authors || []).join(", ") || "作者信息缺失";
    node.querySelector(".paper-abstract").textContent = paper.abstract || "该记录没有抽取到摘要。";

    const tagBox = node.querySelector(".paper-tags");
    (paper.publication_types || []).slice(0, 3).forEach((item) => {
      tagBox.append(createChip(item));
    });
    if (paper.pmid) {
      tagBox.append(createChip(`PMID ${paper.pmid}`));
    }

    const linkBox = node.querySelector(".paper-links");
    if (paper.pubmed_url) {
      linkBox.append(createLink(paper.pubmed_url, "PubMed"));
    }
    if (paper.doi_url) {
      linkBox.append(createLink(paper.doi_url, "DOI"));
    }

    fragment.append(node);
  });

  elements.papersGrid.append(fragment);
}

function renderPushState(cache, nextPushAt) {
  elements.nextPush.textContent = nextPushAt ? formatDate(nextPushAt) : "未开启";

  const baseMessage = cache.last_push_error
    ? `最近推送失败：${cache.last_push_error}`
    : cache.last_push_message || "微信推送未开启。";
  elements.pushMessage.textContent = baseMessage;

  const countText = `${cache.last_push_count || 0} 篇`;
  const timeText = formatDate(cache.last_push_at);
  elements.pushMeta.textContent = `最近推送时间：${timeText}，本次涉及：${countText}`;
}

function renderDashboard(snapshot) {
  const { config, cache, next_refresh_at: nextRefreshAt, next_push_at: nextPushAt } = snapshot;
  fillConfig(config);
  state.papers = cache.papers || [];

  elements.paperCount.textContent = `${cache.count || 0} 篇`;
  elements.lastSuccess.textContent = formatDate(cache.last_success_at);
  elements.nextRefresh.textContent = formatDate(nextRefreshAt);
  elements.feedMessage.textContent = cache.error
    ? `最近刷新出错：${cache.error}`
    : cache.message || "缓存已更新。";

  renderPushState(cache, nextPushAt);
  renderStatus(cache.status, cache.message);
  renderPapers();
}

async function requestJson(url, options = {}) {
  const response = await fetch(resolvePath(url), {
    headers: {
      "Content-Type": "application/json",
    },
    ...options,
  });

  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "请求失败");
  }
  return payload;
}

async function loadDashboard() {
  try {
    const snapshot = await requestJson("api/dashboard");
    state.mode = "server";
    renderDashboard(snapshot);
    return;
  } catch (serverError) {
    const snapshot = await requestJson("data/dashboard.json");
    setStaticMode();
    renderDashboard(snapshot);
    elements.feedMessage.textContent = "当前为在线只读模式。论文列表由 GitHub Actions 每日自动更新。";
    elements.pushMeta.textContent = "在线站点不提供密钥录入或测试推送，微信推送由仓库 Secrets 和定时任务负责。";
  }
}

function shouldPauseBackgroundSync() {
  const activeElement = document.activeElement;
  return Boolean(activeElement && elements.configForm.contains(activeElement));
}

async function handleRefresh() {
  setBusy(true);
  renderStatus("loading", "正在刷新 PubMed...");
  try {
    const snapshot = await requestJson("api/refresh", { method: "POST" });
    renderDashboard(snapshot);
  } catch (error) {
    renderStatus("error", error.message || "刷新失败");
    elements.feedMessage.textContent = error.message || "刷新失败";
  } finally {
    setBusy(false);
  }
}

async function handleConfigSubmit(event) {
  event.preventDefault();
  setBusy(true);
  renderStatus("loading", "正在保存配置并刷新...");

  const payload = {
    search_query: elements.searchQuery.value.trim(),
    max_results: Number(elements.maxResults.value),
    lookback_days: Number(elements.lookbackDays.value),
    refresh_interval_hours: Number(elements.refreshInterval.value),
    contact_email: elements.contactEmail.value.trim(),
    push_enabled: elements.pushEnabled.checked,
    push_channel: elements.pushChannel.value,
    push_time: elements.pushTime.value,
    push_timezone: elements.pushTimezone.value.trim(),
    push_max_papers: Number(elements.pushMaxPapers.value),
    serverchan_sendkey: elements.serverchanSendkey.value.trim(),
    wecom_webhook_url: elements.wecomWebhookUrl.value.trim(),
  };

  try {
    const snapshot = await requestJson("api/config", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderDashboard(snapshot);
  } catch (error) {
    renderStatus("error", error.message || "配置保存失败");
    elements.feedMessage.textContent = error.message || "配置保存失败";
  } finally {
    setBusy(false);
  }
}

async function handlePushTest() {
  setBusy(true);
  elements.pushMessage.textContent = "正在发送测试微信推送...";
  try {
    const snapshot = await requestJson("api/push-test", { method: "POST" });
    renderDashboard(snapshot);
  } catch (error) {
    elements.pushMessage.textContent = error.message || "测试推送失败";
  } finally {
    setBusy(false);
  }
}

elements.refreshButton.addEventListener("click", handleRefresh);
elements.pushTestButton.addEventListener("click", handlePushTest);
elements.pushChannel.addEventListener("change", syncPushChannelFields);
elements.configForm.addEventListener("submit", handleConfigSubmit);
elements.keywordFilter.addEventListener("input", (event) => {
  state.filterText = event.target.value.trim();
  renderPapers();
});

loadDashboard().catch((error) => {
  renderStatus("error", "初始化失败");
  elements.feedMessage.textContent = error.message || "读取初始数据失败";
});

window.setInterval(() => {
  if (state.mode === "static") {
    loadDashboard().catch(() => {});
    return;
  }
  if (shouldPauseBackgroundSync()) {
    return;
  }
  loadDashboard().catch(() => {});
}, 300000);
