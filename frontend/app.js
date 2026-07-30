const sessions = {
  a: {
    seconds: 0,
    roundLabel: "第 1 轮 · 李江单方会谈",
    speaker: "李江",
    utterances: [],
    extract: [],
  },
  b: {
    seconds: 0,
    roundLabel: "第 1 轮 · 周枫单方会谈",
    speaker: "周枫",
    utterances: [],
    extract: [],
  },
  joint: {
    seconds: 0,
    roundLabel: "第 1 轮 · 双方共同确认",
    speaker: "双方",
    utterances: [],
    extract: [],
  },
};

const defaultDemandTopics = ["道歉", "赔偿金额", "履行方式", "后续承诺", "其他"];

function createEmptyDemandRows() {
  return defaultDemandTopics.map((topic) => [topic, "", "", "待采集", "amber"]);
}

const appState = {
  currentPage: "voice",
  currentSession: "a",
  currentCompare: null,
  currentDocument: "agreement",
  currentSigner: null,
  currentAnalysis: null,
  caseState: null,
  // 诉求重新提取后，旧的一致性结论先标记为待刷新，避免页面在同一人二次提取时反复套用旧状态。
  analysisDirty: false,
  extractingDemand: false,
  analyzingDemand: false,
  demandStore: {
    a: {},
    b: {},
  },
  // 运行期数据只存用户本次采集到的内容。
  sessionRuntime: {
    a: { transcript: [], extraction: null, speakerMeta: {}, speakerCount: 0 },
    b: { transcript: [], extraction: null, speakerMeta: {}, speakerCount: 0 },
    joint: { transcript: [], extraction: null, speakerMeta: {}, speakerCount: 0 },
  },
  roundArchive: {
    a: [],
    b: [],
    joint: [],
  },
  asr: {
    socket: null,
    stream: null,
    context: null,
    source: null,
    analyser: null,
    audioData: null,
    meterAnimation: null,
    audioLevel: 0,
    processor: null,
    closing: false,
    reconnectAttempts: 0,
    reconnectTimer: null,
  },
  roundState: {
    a: { index: 1, running: false, stopped: false, seconds: 0, stoppedSeconds: null, startedAt: null, endedAt: null },
    b: { index: 1, running: false, stopped: false, seconds: 0, stoppedSeconds: null, startedAt: null, endedAt: null },
    joint: { index: 1, running: false, stopped: false, seconds: 0, stoppedSeconds: null, startedAt: null, endedAt: null },
  },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const fallbackParties = [
  { name: "李江", gender: "男", id_no: "330108********2134", identity: "受害人 / 报警人", work_unit: "/", occupation: "/", home_address: "/" },
  { name: "周枫", gender: "男", id_no: "330108********7788", identity: "嫌疑人", work_unit: "/", occupation: "/", home_address: "/" },
];

function formatChineseDate(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

function formatTime(totalSeconds) {
  const hours = Math.floor(totalSeconds / 3600).toString().padStart(2, "0");
  const minutes = Math.floor((totalSeconds % 3600) / 60).toString().padStart(2, "0");
  const seconds = Math.floor(totalSeconds % 60).toString().padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

function formatAbsoluteTime(value = Date.now()) {
  const date = value instanceof Date ? value : new Date(value);
  const pad = (input) => String(input).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join("-") + ` ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function getParties(caseState = appState.caseState) {
  const parties = Array.isArray(caseState?.parties) ? caseState.parties : [];
  return [parties[0] || fallbackParties[0], parties[1] || fallbackParties[1]];
}

function getPartyBySession(sessionKey, caseState = appState.caseState) {
  const [partyA, partyB] = getParties(caseState);
  return sessionKey === "b" ? partyB : partyA;
}

function buildRoundLabel(sessionKey, startedAt = null) {
  const session = sessions[sessionKey];
  const state = appState.roundState[sessionKey];
  const label = `第 ${state.index} 轮 · ${session.speaker}单方会谈`;
  return startedAt ? `${label} - ${formatAbsoluteTime(startedAt)}` : label;
}

function cloneTranscript(transcript) {
  return transcript.map((item) => ({ ...item }));
}

function archiveCurrentRound(sessionKey) {
  const state = appState.roundState[sessionKey];
  const runtime = currentRuntime(sessionKey);
  if (!runtime.transcript.length) return;

  const archive = appState.roundArchive[sessionKey];
  const snapshot = {
    index: state.index,
    label: sessions[sessionKey].roundLabel,
    startedAt: state.startedAt,
    endedAt: state.endedAt,
    transcript: cloneTranscript(runtime.transcript),
    extraction: runtime.extraction ? { ...runtime.extraction } : null,
  };
  const last = archive[archive.length - 1];
  if (last && last.index === snapshot.index && last.startedAt === snapshot.startedAt) {
    Object.assign(last, snapshot);
    return;
  }
  archive.push(snapshot);
}

function transcriptToText(transcript) {
  return transcript.map((item) => `${item.speaker}: ${item.text}`).join("\n");
}

function collectSessionTranscript(sessionKey) {
  const state = appState.roundState[sessionKey];
  const runtime = currentRuntime(sessionKey);
  const sections = appState.roundArchive[sessionKey]
    .filter((round) => round.transcript.length)
    .map((round) => `【${round.label}】\n${transcriptToText(round.transcript)}`);
  const currentAlreadyArchived = appState.roundArchive[sessionKey]
    .some((round) => round.index === state.index && round.startedAt === state.startedAt);

  if (runtime.transcript.length && !currentAlreadyArchived) {
    sections.push(`【${sessions[sessionKey].roundLabel}】\n${transcriptToText(runtime.transcript)}`);
  }
  return sections.join("\n\n");
}

function collectCurrentRoundTranscript(sessionKey) {
  const state = appState.roundState[sessionKey];
  const runtime = currentRuntime(sessionKey);
  const currentRound = appState.roundArchive[sessionKey]
    .find((round) => round.index === state.index && round.startedAt === state.startedAt);
  const transcript = currentRound?.transcript?.length ? currentRound.transcript : runtime.transcript;
  if (!transcript.length) return "";
  // 诉求提取只看当前人员本轮对话；历史结论通过 current_extraction/current_demand 传入，避免把另一方内容带入提取。
  return `【${sessions[sessionKey].roundLabel}】\n${transcriptToText(transcript)}`;
}

function toast(message) {
  const node = $("#toast");
  if (!node) return;
  node.textContent = message;
  node.classList.add("show");
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => node.classList.remove("show"), 2200);
}

async function readJsonResponse(response, fallbackMessage) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || fallbackMessage);
  }
  return payload;
}

async function getPublicConfig() {
  try {
    return await fetch("/api/config").then((response) => response.json());
  } catch (error) {
    return {};
  }
}

async function getCaseState() {
  try {
    const payload = await fetch("/api/state").then((response) => response.json());
    appState.caseState = payload;
    return payload;
  } catch (error) {
    return appState.caseState || {};
  }
}

function collectDemandRowsForDraft() {
  return $$("#live-compare-body tr").map((row) => {
    const cells = $$("td", row);
    return [
      cells[0]?.textContent?.trim() || "",
      cells[1]?.textContent?.trim() || "",
      cells[2]?.textContent?.trim() || "",
      cells[3]?.textContent?.trim() || "",
    ];
  }).filter((row) => row.some(Boolean));
}

function demandRowsToAgreedTerms(rows) {
  return rows
    .filter(([, aText, bText]) => aText || bText)
    .map(([topic, aText, bText, status]) => ({
      topic,
      party_a: aText,
      party_b: bText,
      status,
    }));
}

function hasBothPartyDemands(rows) {
  // 生成协议条款必须有双方诉求；任一方整列为空时不调用大模型。
  const hasPartyA = rows.some(([, aText]) => Boolean(aText && aText.trim()));
  const hasPartyB = rows.some(([, , bText]) => Boolean(bText && bText.trim()));
  return hasPartyA && hasPartyB;
}

function formatPartyWork(party) {
  const workUnit = party?.work_unit || "/";
  const occupation = party?.occupation || "/";
  if (workUnit === "/" && occupation === "/") return "/";
  return `${workUnit} / ${occupation}`;
}

function applyCasePartiesToUi() {
  const [partyA, partyB] = getParties();
  sessions.a.speaker = partyA.name || fallbackParties[0].name;
  sessions.b.speaker = partyB.name || fallbackParties[1].name;
  sessions.a.roundLabel = buildRoundLabel("a", appState.roundState.a.startedAt);
  sessions.b.roundLabel = buildRoundLabel("b", appState.roundState.b.startedAt);

  $$(".segment").forEach((button) => {
    const party = getPartyBySession(button.dataset.session);
    const label = $("span", button);
    if (label && party?.name) label.textContent = party.name;
  });

  const demandHeaders = $$(".demand-table thead th");
  if (demandHeaders[1]) demandHeaders[1].textContent = sessions.a.speaker;
  if (demandHeaders[2]) demandHeaders[2].textContent = sessions.b.speaker;

  const signBoxes = $$(".sign-box");
  if (signBoxes[0]) {
    signBoxes[0].dataset.signer = `${sessions.a.speaker}签名`;
    $("span", signBoxes[0]).textContent = `${sessions.a.speaker}签名`;
  }
  if (signBoxes[1]) {
    signBoxes[1].dataset.signer = `${sessions.b.speaker}签名`;
    $("span", signBoxes[1]).textContent = `${sessions.b.speaker}签名`;
  }
}

function normalizeDraftClauses(content) {
  const clauses = Array.isArray(content?.clauses) ? content.clauses : [];
  return clauses.map((clause) => {
    if (typeof clause === "string") return clause.trim();
    if (clause && typeof clause === "object") {
      return String(clause.value || clause.content || clause.text || clause.item || "").trim();
    }
    return "";
  }).filter(Boolean);
}

function normalizeRecordStatements(content) {
  const statements = Array.isArray(content?.statements) ? content.statements : [];
  return statements.map((item) => {
    if (typeof item === "string") {
      const [speaker, ...rest] = item.split(/[：:]/);
      return { speaker: speaker?.trim() || "当事人", content: rest.join("：").trim() || item.trim() };
    }
    if (item && typeof item === "object") {
      return {
        speaker: String(item.speaker || item.name || "当事人").trim(),
        content: String(item.content || item.text || item.summary || "").trim(),
      };
    }
    return { speaker: "", content: "" };
  }).filter((item) => item.speaker && item.content);
}

function cnOrder(index) {
  return ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"][index] || String(index + 1);
}

function splitStaffNames(value = "") {
  const names = String(value || "")
    .split(/[、,，/]/)
    .map((item) => item.trim())
    .filter(Boolean);
  return {
    host: names[0] || "徐明华",
    recorder: names[1] || "陈宪忠",
  };
}

function firstRoundTime(rounds = []) {
  const first = rounds.find((round) => round.startedAt);
  return first?.startedAt ? formatAbsoluteTime(first.startedAt) : formatAbsoluteTime();
}

function collectRecordRounds() {
  const groups = [];
  ["a", "b", "joint"].forEach((sessionKey) => {
    const session = sessions[sessionKey];
    const state = appState.roundState[sessionKey];
    const runtime = currentRuntime(sessionKey);
    appState.roundArchive[sessionKey]
      .filter((round) => round.transcript.length)
      .forEach((round) => groups.push({
        session_key: sessionKey,
        speaker: session.speaker,
        label: round.label,
        startedAt: round.startedAt,
        transcript: cloneTranscript(round.transcript),
        transcript_text: transcriptToText(round.transcript),
      }));

    const currentAlreadyArchived = appState.roundArchive[sessionKey]
      .some((round) => round.index === state.index && round.startedAt === state.startedAt);
    if (runtime.transcript.length && !currentAlreadyArchived) {
      groups.push({
        session_key: sessionKey,
        speaker: session.speaker,
        label: session.roundLabel,
        startedAt: state.startedAt,
        transcript: cloneTranscript(runtime.transcript),
        transcript_text: transcriptToText(runtime.transcript),
      });
    }
  });
  return groups.sort((left, right) => {
    const leftTime = left.startedAt ? new Date(left.startedAt).getTime() : Number.MAX_SAFE_INTEGER;
    const rightTime = right.startedAt ? new Date(right.startedAt).getTime() : Number.MAX_SAFE_INTEGER;
    return leftTime - rightTime;
  });
}

function renderAgreementDraft(content = {}, caseState = {}) {
  const [partyA, partyB] = getParties(caseState);
  const illegalFact = caseState.illegal_fact || $(".case-brief-grid .wide b")?.textContent?.trim() || "待根据案件基本信息生成。";
  const clauses = normalizeDraftClauses(content);
  const clauseHtml = clauses
    .map((clause, index) => `<p class="doc-indent">${cnOrder(index)}、${escapeHTML(clause)}</p>`)
    .join("") || `<p class="doc-empty-line"></p>`;

  $("#document-title").textContent = "调解协议书";
  $("#document-paper").innerHTML = `
    <article class="official-document">
      <h3>现场治安调解协调书</h3>
      <section class="doc-section">
        <p class="doc-section-title">人员基本信息</p>
        <p>当事人：${escapeHTML(partyA.name)}　性别：${escapeHTML(partyA.gender)}　身份证及号码：${escapeHTML(partyA.id_no || "/")}</p>
        <p>工作单位及职业：${escapeHTML(formatPartyWork(partyA))}</p>
        <p>家庭地址：${escapeHTML(partyA.home_address || "/")}</p>
        <p>当事人：${escapeHTML(partyB.name)}　性别：${escapeHTML(partyB.gender)}　身份证及号码：${escapeHTML(partyB.id_no || "/")}</p>
        <p>工作单位及职业：${escapeHTML(formatPartyWork(partyB))}</p>
        <p>家庭地址：${escapeHTML(partyB.home_address || "/")}</p>
      </section>
      <section class="doc-section">
        <p class="doc-section-title">主要事实</p>
        <p class="doc-indent">${escapeHTML(illegalFact)}</p>
      </section>
      <section class="doc-section">
        <p class="doc-section-title">经调解，双方自愿达成如下协议：</p>
        ${clauseHtml}
        <p class="doc-indent">本协议自双方签字之时起生效，并当场履行，公安机关对违反治安管理行为人不予处罚。</p>
      </section>
      <section class="doc-signatures">
        <p>当事人（${escapeHTML(partyA.name)}）：</p>
        <p>当事人（${escapeHTML(partyB.name)}）：</p>
        <p>办案民警：</p>
        <p class="doc-date-right">日期：${formatChineseDate()}</p>
      </section>
    </article>
  `;
}

function renderRecordDraft(content = {}, caseState = {}, rounds = []) {
  const parties = Array.isArray(caseState.parties) ? caseState.parties : [];
  const partyNames = parties.map((party) => party.name).filter(Boolean).join("、") || "当事人";
  const staff = splitStaffNames(caseState.created_by);
  const workUnit = caseState.police_unit || "杭州市公安局滨江区分局";
  const recordTime = content.record_time || firstRoundTime(rounds);
  const mediationPlace = content.place || caseState.mediation_place || caseState.location || "杭州市公安局滨江区分局";
  const statements = normalizeRecordStatements(content);
  const shouldRenderGeneratedContent = content.skip_generated_content !== true;
  const fallbackStatements = shouldRenderGeneratedContent ? rounds.flatMap((round) => round.transcript.map((item) => ({
    speaker: item.speaker || round.speaker,
    content: item.text || "",
  }))).filter((item) => item.speaker && item.content) : [];
  const recordStatements = shouldRenderGeneratedContent ? (statements.length ? statements : fallbackStatements) : [];
  const statementHtml = recordStatements
    .map((item) => `<p><span>${escapeHTML(item.speaker)}：</span>${escapeHTML(item.content)}</p>`)
    .join("");

  $("#document-title").textContent = "调解笔录";
  $("#document-paper").innerHTML = `
    <article class="official-document official-record">
      <h3>调解笔录</h3>
      <section class="record-basic-info">
        <p><span>时间：</span><b>${escapeHTML(recordTime)}</b></p>
        <p><span>调解地点：</span><b>${escapeHTML(mediationPlace)}</b></p>
        <p><span>主持人：</span><b>${escapeHTML(staff.host)}</b><span>工作单位：</span><b>${escapeHTML(workUnit)}</b></p>
        <p><span>记录员：</span><b>${escapeHTML(staff.recorder)}</b><span>工作单位：</span><b>${escapeHTML(workUnit)}</b></p>
      </section>
      <section class="record-content">
        ${statementHtml}
        <p><span>主持人：</span>经双方当事人协商，无法达成一致的协议。我们派出所将根据治安管理处罚的有关规定进行行政处罚。</p>
        <p>以上笔录请你们细阅，如有遗漏或错误，请指正，我们给予更正，如没有错误。</p>
      </section>
      <section class="record-signature-sheet">
        <p>请签字确认（按印）</p>
        <div class="record-sign-row">
          <span>当事人：${escapeHTML(partyNames)}</span>
          <span>${escapeHTML(formatChineseDate())}</span>
        </div>
        <div class="record-sign-row">
          <span>主持人：${escapeHTML(staff.host)}</span>
          <span>记录员：${escapeHTML(staff.recorder)}</span>
        </div>
      </section>
    </article>
  `;
}

function renderDocumentLoading(title = "调解协议书", message = "正在生成协议内容...") {
  $("#document-title").textContent = title;
  $("#document-paper").innerHTML = `<div class="document-loading">${escapeHTML(message)}</div>`;
}

async function generateDocumentDraft(key = "agreement") {
  appState.currentDocument = key;
  const docType = key === "record" ? "MEDIATION_RECORD" : "MEDIATION_AGREEMENT";
  const title = key === "record" ? "调解笔录" : "调解协议书";
  renderDocumentLoading(title, key === "record" ? "正在生成调解笔录..." : "正在生成调解协议...");

  const caseState = await getCaseState();
  if (key === "record") {
    const rounds = collectRecordRounds();
    const demandRows = collectDemandRowsForDraft();
    if (!hasBothPartyDemands(demandRows)) {
      renderRecordDraft({ statements: [], skip_generated_content: true }, caseState, rounds);
      toast("双方诉求未采集完整，暂不调用大模型生成笔录调解内容");
      return;
    }
    if (!rounds.length) {
      renderRecordDraft({ statements: [], skip_generated_content: true }, caseState, rounds);
      toast("暂无会谈转写，已生成基础笔录模板");
      return;
    }
    const response = await fetch("/api/documents/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        doc_type: docType,
        agreed_terms: [],
        analysis: appState.currentAnalysis || {},
        demand_rows: demandRows,
        rounds,
      }),
    });
    const payload = await readJsonResponse(response, "调解笔录生成失败");
    renderRecordDraft(payload.document?.content || {}, caseState, rounds);
    return;
  }

  const demandRows = collectDemandRowsForDraft();
  if (!hasBothPartyDemands(demandRows)) {
    renderAgreementDraft({ clauses: [] }, caseState);
    toast("双方诉求未采集完整，暂不调用大模型生成协议内容");
    return;
  }
  const response = await fetch("/api/documents/draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      doc_type: docType,
      agreed_terms: demandRowsToAgreedTerms(demandRows),
      analysis: appState.currentAnalysis || {},
      demand_rows: demandRows,
    }),
  });
  const payload = await readJsonResponse(response, "调解协议生成失败");
  renderAgreementDraft(payload.document?.content || {}, caseState);
}

function nextSimulationSession(config, sessionKey, roundIndex) {
  const sequence = Array.isArray(config.realtime_asr_simulation_sequence)
    ? config.realtime_asr_simulation_sequence
    : [];
  let occurrence = 0;
  for (let index = 0; index < sequence.length; index += 1) {
    if (sequence[index] !== sessionKey) continue;
    occurrence += 1;
    if (occurrence === roundIndex) return sequence[index + 1] || null;
  }
  return null;
}

async function advanceSimulationSession(sessionKey, roundIndex) {
  const config = await getPublicConfig();
  if (!config.realtime_asr_simulation) return;
  const nextSession = nextSimulationSession(config, sessionKey, roundIndex);
  if (!nextSession || nextSession === appState.currentSession) return;
  updateSession(nextSession);
  toast(`已切换到${sessions[nextSession].speaker}下一段模拟会谈`);
}

function setDemandExtracting(active, message = "诉求提取中") {
  appState.extractingDemand = active;
  const node = $("#demand-extract-modal");
  if (!node) return;
  const text = $("#demand-extract-text");
  if (text) text.textContent = message;
  node.classList.toggle("show", active);
  node.setAttribute("aria-hidden", active ? "false" : "true");
}

function setAnalysisAnalyzing(active, message = "一致性分析中") {
  appState.analyzingDemand = active;
  const node = $("#analysis-modal");
  if (!node) return;
  const text = $("#analysis-text");
  if (text) text.textContent = message;
  node.classList.toggle("show", active);
  node.setAttribute("aria-hidden", active ? "false" : "true");
}

function waitNextPaint() {
  return new Promise((resolve) => {
    // 诉求提取完成后先让浏览器把新诉求绘制出来，再继续一致性分析，避免用户看到表格一直停在旧状态。
    window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
  });
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[char]));
}

function currentRuntime(sessionKey = appState.currentSession) {
  return appState.sessionRuntime[sessionKey] || appState.sessionRuntime.a;
}

function resetRuntime(sessionKey) {
  appState.sessionRuntime[sessionKey] = {
    transcript: [],
    extraction: null,
    speakerMeta: {},
    speakerCount: 0,
  };
}

function scrollStatementChatToBottom() {
  const body = $("#page-voice .statement-chat-body");
  if (!body) return;
  window.requestAnimationFrame(() => {
    body.scrollTop = body.scrollHeight;
  });
}

function resolveSpeakerMeta(sessionKey, speakerId) {
  const numericSpeakerId = Number(speakerId);
  if (!Number.isFinite(numericSpeakerId) || numericSpeakerId < 0) {
    return { label: sessions[sessionKey].speaker, speakerClass: "party-a" };
  }

  const runtime = currentRuntime(sessionKey);
  const key = String(numericSpeakerId);
  if (!runtime.speakerMeta[key]) {
    const speakerCount = (runtime.speakerCount || 0) + 1;
    runtime.speakerCount = speakerCount;
    runtime.speakerMeta[key] = {
      label: `说话人${speakerCount}`,
      speakerClass: `speaker-${((speakerCount - 1) % 4) + 1}`,
    };
  }
  return runtime.speakerMeta[key];
}

function classifyUtteranceRole(text, sessionKey) {
  if (/^(POLICE|民警)/i.test(text.trim())) return "police";
  return sessionKey === "b" ? "party-b" : "party-a";
}

function renderRuntimeUtterances(sessionKey) {
  const session = sessions[sessionKey];
  const runtime = currentRuntime(sessionKey);
  const state = appState.roundState[sessionKey];
  const currentAlreadyArchived = appState.roundArchive[sessionKey]
    .some((round) => round.index === state.index && round.startedAt === state.startedAt);
  const groups = [
    ...appState.roundArchive[sessionKey]
      .filter((round) => round.transcript.length)
      .map((round) => ({ label: round.label, transcript: round.transcript })),
  ];

  if ((runtime.transcript.length || state.startedAt) && !currentAlreadyArchived) {
    groups.push({ label: session.roundLabel, transcript: runtime.transcript });
  }

  if (!groups.length) {
    $("#utterances").innerHTML = "";
    scrollStatementChatToBottom();
    return;
  }

  $("#utterances").innerHTML = groups.map((group) => [
    `<div class="round-divider"><span>${escapeHTML(group.label)}</span></div>`,
    ...group.transcript.map((item) => (
      `<div class="utterance ${item.role} ${item.speakerClass || ""}"><span>${escapeHTML(item.speaker)}</span><p>${escapeHTML(item.text)}</p></div>`
    )),
  ].join("")).join("");
  scrollStatementChatToBottom();
}

function clearDemandCompare() {
  renderDemandRows([], "");
  const compareScript = $("#compare-joint-script");
  if (compareScript) compareScript.textContent = "";
  const status = $("#compare-live-status");
  if (status) {
    status.textContent = "待采集";
    status.className = "tag amber";
  }
}

function normalizeDemandTopic(rawTopic, text = "") {
  const value = `${rawTopic || ""} ${text || ""}`.toUpperCase();
  if (/COMPENSATION|赔|金额|费用|票据|医疗/.test(value)) return "赔偿金额";
  if (/APOLOGY|道歉|致歉|赔礼/.test(value)) return "道歉";
  if (/PERFORM|履行|支付|付清|一次性|当场|期限/.test(value)) return "履行方式";
  if (/PROMISE|承诺|保证|不再|后续|滋扰|冲突/.test(value)) return "后续承诺";
  return "其他";
}

function mergeDemandItem(items, topic, text) {
  const cleanText = String(text || "").trim();
  if (!cleanText) return;
  const cleanTopic = normalizeDemandTopic(topic, cleanText);
  if (!items[cleanTopic]) {
    items[cleanTopic] = cleanText;
    return;
  }
  if (!items[cleanTopic].includes(cleanText)) {
    items[cleanTopic] = `${items[cleanTopic]}；${cleanText}`;
  }
}

function normalizeModelList(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === "string" && value.trim()) return [{ description: value.trim() }];
  if (value && typeof value === "object") return [value];
  return [];
}

function extractionToDemandItems(extraction) {
  const items = {};
  const claims = normalizeModelList(extraction?.claims);
  const concessions = normalizeModelList(extraction?.concessions);
  claims.forEach((item) => {
    mergeDemandItem(items, item.type || item.topic || "诉求", item.description || item.statement || item.content || "");
  });
  concessions.forEach((item) => {
    mergeDemandItem(items, item.type || item.topic || "让步/承诺", item.description || item.statement || item.content || "");
  });
  if (extraction?.attitude) {
    const note = typeof extraction.attitude === "string"
      ? extraction.attitude
      : extraction.attitude.note || extraction.attitude.willingness || "";
    mergeDemandItem(items, "其他", note);
  }
  return items;
}

function demandSide(sessionKey) {
  return sessionKey === "a" || sessionKey === "b" ? sessionKey : null;
}

function updateDemandStore(sessionKey, extraction) {
  const side = demandSide(sessionKey);
  if (!side) return;
  const existing = appState.demandStore[side] || {};
  appState.demandStore[side] = {
    ...existing,
    ...extractionToDemandItems(extraction),
  };
}

function latestExtraction(sessionKey) {
  const runtimeExtraction = currentRuntime(sessionKey).extraction;
  if (runtimeExtraction) return runtimeExtraction;
  const archive = appState.roundArchive[sessionKey] || [];
  for (let index = archive.length - 1; index >= 0; index -= 1) {
    if (archive[index].extraction) return archive[index].extraction;
  }
  return null;
}

function hasDemandStoreContent() {
  return Object.values(appState.demandStore).some((items) => Object.keys(items || {}).length);
}

function buildDemandRowsFromStore(overrides = {}, pendingResult = "待比对", pendingColor = "blue") {
  const aItems = appState.demandStore.a || {};
  const bItems = appState.demandStore.b || {};
  const topics = defaultDemandTopics;

  return topics.map((topic) => {
    const aText = aItems[topic] || "";
    const bText = bItems[topic] || "";
    const override = overrides[topic];
    if (override) return [topic, aText, bText, override.result, override.color];
    if (!aText && !bText) return [topic, "", "", "待采集", "amber"];
    if (!aText || !bText) return [topic, aText, bText, "待采集", "amber"];
    return [topic, aText, bText, pendingResult, pendingColor];
  });
}

function collectDemandStatusSnapshot() {
  const snapshot = {};
  $$("#live-compare-body tr").forEach((row) => {
    const cells = $$("td", row);
    const topic = cells[0]?.textContent?.trim();
    const tag = $("span.tag", cells[3]);
    if (!topic || !tag) return;
    const color = Array.from(tag.classList).find((name) => name !== "tag") || "blue";
    snapshot[topic] = {
      result: tag.textContent.trim(),
      color,
    };
  });
  return snapshot;
}

function applyDemandStatusSnapshot(rows, snapshot = {}) {
  return rows.map(([topic, aText, bText, result, color]) => {
    const status = snapshot[topic];
    if (!status) return [topic, aText, bText, result, color];
    // 提取阶段只刷新双方诉求文本，状态列沿用分析前的状态；分析接口完成后再统一更新状态。
    return [topic, aText, bText, status.result, status.color];
  });
}

function buildDemandRowsPreservingStatus(snapshot = collectDemandStatusSnapshot()) {
  return applyDemandStatusSnapshot(buildDemandRowsFromStore(), snapshot);
}

function normalizeClaims(extraction) {
  const claims = normalizeModelList(extraction?.claims);
  const concessions = normalizeModelList(extraction?.concessions);
  return [
    ...claims.map((item) => ({
      topic: item.type || "诉求",
      text: item.description || item.statement || item.content || "",
    })),
    ...concessions.map((item) => ({
      topic: "让步/承诺",
      text: item.description || item.statement || item.content || "",
    })),
  ].filter((item) => item.text);
}

function renderDemandRows(rows, summary = "") {
  const html = rows.map(([topic, aText, bText, result, color]) => (
    `<tr><td><div class="demand-cell-scroll topic-cell">${escapeHTML(topic)}</div></td><td><div class="demand-cell-scroll">${escapeHTML(aText)}</div></td><td><div class="demand-cell-scroll">${escapeHTML(bText)}</div></td><td><span class="tag ${color}">${escapeHTML(result)}</span></td></tr>`
  )).join("");
  $("#live-compare-body").innerHTML = html;
  $("#decision-next-text").textContent = summary;
  const compareScript = $("#compare-joint-script");
  if (compareScript) compareScript.textContent = summary;
}

function renderSingleSideDemand(sessionKey, extraction) {
  const statusSnapshot = collectDemandStatusSnapshot();
  updateDemandStore(sessionKey, extraction);
  renderDemandRows(buildDemandRowsPreservingStatus(statusSnapshot), "已根据本轮会谈更新诉求，等待另一方会谈后形成对照。");
}

function renderAnalysisDemand(analysis) {
  const overrides = {};
  normalizeModelList(analysis.commonGrounds).forEach((item) => {
    const topic = normalizeDemandTopic(item.topic || "一致事项", item.detail || item.partyA || item.partyB || item.content || item.description || item.statement || "");
    overrides[topic] = { result: "一致", color: "green" };
  });
  normalizeModelList(analysis.disputePoints).forEach((item) => {
    const topic = normalizeDemandTopic(item.topic || "分歧事项", `${item.partyA || ""} ${item.partyB || ""} ${item.content || ""} ${item.description || ""} ${item.statement || ""}`);
    overrides[topic] = { result: "待协商", color: "amber" };
  });
  const rows = buildDemandRowsFromStore(overrides);
  const summary = typeof analysis.feasibility === "string"
    ? analysis.feasibility
    : analysis.feasibility?.suggestedFocus || analysis.feasibility?.reasoning || "双方诉求对照已生成。";
  renderDemandRows(rows, summary);
}

function renderPendingAnalysisDemand(message = "诉求已更新，等待一致性分析。", statusSnapshot = collectDemandStatusSnapshot()) {
  renderDemandRows(buildDemandRowsPreservingStatus(statusSnapshot), message);
}

function appendAsrSentence(payload) {
  const rawText = typeof payload.text === "string"
    ? payload.text
    : payload.text?.voice_text_str || payload.text?.sentence || payload.text?.text || "";
  const text = rawText.trim();
  if (!text) return;

  const sessionKey = appState.currentSession;
  const runtime = currentRuntime(sessionKey);
  const id = payload.sentence_id ?? `sentence-${Date.now()}-${runtime.transcript.length}`;
  const existing = runtime.transcript.find((item) => item.id === id && !item.isFinal);
  const speakerMeta = resolveSpeakerMeta(sessionKey, payload.speaker_id);
  const speaker = payload.speaker || speakerMeta.label;
  const role = payload.role || (Number(payload.speaker_id) >= 0 ? "speaker" : classifyUtteranceRole(text, sessionKey));

  if (existing) {
    existing.text = text;
    existing.isFinal = Boolean(payload.is_final);
  } else {
    runtime.transcript.push({
      id,
      speaker,
      text,
      role,
      speakerClass: speakerMeta.speakerClass,
      isFinal: Boolean(payload.is_final),
    });
  }
  renderRuntimeUtterances(sessionKey);
}

function handleAsrFailure(message) {
  const state = appState.roundState[appState.currentSession];
  state.running = false;
  stopRealtimeAsr(false).finally(() => {
    renderVoiceSummary(appState.currentSession);
    toast(`实时转写失败：${message}`);
  });
}

function downsampleBuffer(buffer, inputRate, outputRate) {
  if (outputRate === inputRate) return buffer;
  const ratio = inputRate / outputRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  for (let i = 0; i < newLength; i += 1) {
    const start = Math.round(i * ratio);
    const end = Math.round((i + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (let j = start; j < end && j < buffer.length; j += 1) {
      sum += buffer[j];
      count += 1;
    }
    result[i] = count ? sum / count : 0;
  }
  return result;
}

function encodePCM16(buffer) {
  const view = new DataView(new ArrayBuffer(buffer.length * 2));
  buffer.forEach((sample, index) => {
    const clipped = Math.max(-1, Math.min(1, sample));
    view.setInt16(index * 2, clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff, true);
  });
  return view.buffer;
}

function updateAudioMeter(level = 0, active = false) {
  const player = $("#meeting-player");
  const wave = $("#voice-wave");
  const label = $("#audio-level-text");
  if (!player || !wave || !label) return;
  const normalized = Math.max(0, Math.min(1, level));
  wave.style.setProperty("--audio-level", normalized.toFixed(3));
  player.classList.toggle("is-recording", active);
  player.classList.toggle("is-muted", active && normalized < 0.05);
  label.textContent = active ? (normalized < 0.05 ? "收音偏弱" : "收音正常") : "收音待开始";
}

function stopAudioMeter() {
  if (appState.asr.meterAnimation) {
    window.cancelAnimationFrame(appState.asr.meterAnimation);
    appState.asr.meterAnimation = null;
  }
  appState.asr.audioLevel = 0;
  updateAudioMeter(0, false);
}

function startAudioMeter(analyser, audioData) {
  stopAudioMeter();
  const tick = () => {
    if (!analyser || !audioData) return;
    analyser.getByteTimeDomainData(audioData);
    let sum = 0;
    for (const value of audioData) {
      const centered = (value - 128) / 128;
      sum += centered * centered;
    }
    const rms = Math.sqrt(sum / audioData.length);
    appState.asr.audioLevel = Math.min(1, rms * 8);
    updateAudioMeter(appState.asr.audioLevel, true);
    appState.asr.meterAnimation = window.requestAnimationFrame(tick);
  };
  tick();
}

async function startRealtimeAsr() {
  await stopRealtimeAsr(false);
  appState.asr.closing = false;
  const config = await getPublicConfig();
  const simulationEnabled = Boolean(config.realtime_asr_simulation);
  const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
  const sessionParam = encodeURIComponent(appState.currentSession);
  const roundParam = encodeURIComponent(appState.roundState[appState.currentSession].index);
  const socket = new WebSocket(`${wsProtocol}://${window.location.host}/api/asr/realtime?session=${sessionParam}&round=${roundParam}`);
  appState.asr.socket = socket;

  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "sentence") {
      appState.asr.reconnectAttempts = 0;
      appendAsrSentence(payload);
    }
    if (payload.type === "error" && !appState.asr.closing) handleAsrFailure(payload.message || "服务连接异常");
  });

  socket.addEventListener("close", () => {
    const state = appState.roundState[appState.currentSession];
    if (appState.asr.closing || !state.running || appState.asr.socket !== socket) return;
    if (appState.asr.reconnectAttempts >= 3) {
      const hasTranscript = currentRuntime(appState.currentSession).transcript.length > 0;
      state.running = false;
      stopRealtimeAsr(false).finally(() => {
        renderVoiceSummary(appState.currentSession);
        toast(hasTranscript ? "实时转写连接中断，请重新开始" : "没有检测到有效人声，请检查麦克风后重新开始");
      });
      return;
    }
    appState.asr.reconnectAttempts += 1;
    toast("实时转写连接中断，正在重连");
    appState.asr.reconnectTimer = window.setTimeout(() => {
      if (appState.roundState[appState.currentSession].running && appState.asr.socket === socket) {
        startRealtimeAsr().catch(() => toast("实时转写重连失败，请重新开始"));
      }
    }, 900);
  });

  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });

  if (simulationEnabled) {
    toast("实时语音模拟已开启");
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    const context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 256;
    const audioData = new Uint8Array(analyser.fftSize);
    const processor = context.createScriptProcessor(4096, 1, 1);

    // 浏览器采集到的浮点音频转成腾讯云实时 ASR 要求的 16k/16bit/单声道 PCM。
    processor.onaudioprocess = (event) => {
      const state = appState.roundState[appState.currentSession];
      if (!state.running || socket.readyState !== WebSocket.OPEN) return;
      const channel = event.inputBuffer.getChannelData(0);
      const pcm = encodePCM16(downsampleBuffer(channel, context.sampleRate, 16000));
      socket.send(pcm);
    };

    source.connect(analyser);
    source.connect(processor);
    processor.connect(context.destination);
    Object.assign(appState.asr, { stream, context, source, analyser, audioData, processor });
    startAudioMeter(analyser, audioData);
  } catch (error) {
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close(1000, "microphone unavailable");
    }
    stopAudioMeter();
    throw error;
  }
}

async function stopRealtimeAsr(sendEnd = true) {
  const { socket, stream, context, source, analyser, processor } = appState.asr;
  appState.asr.closing = true;
  stopAudioMeter();
  if (appState.asr.reconnectTimer) {
    window.clearTimeout(appState.asr.reconnectTimer);
    appState.asr.reconnectTimer = null;
  }
  if (processor) processor.disconnect();
  if (analyser) analyser.disconnect();
  if (source) source.disconnect();
  if (stream) stream.getTracks().forEach((track) => track.stop());
  if (context && context.state !== "closed") await context.close();
  if (socket && socket.readyState === WebSocket.OPEN) {
    if (sendEnd) socket.send(JSON.stringify({ type: "end" }));
    window.setTimeout(() => socket.close(), 600);
  }
  Object.assign(appState.asr, { socket: null, stream: null, context: null, source: null, analyser: null, audioData: null, processor: null });
  window.setTimeout(() => {
    appState.asr.closing = false;
  }, 800);
}

async function generateDemandAfterRound(sessionKey) {
  const runtime = currentRuntime(sessionKey);
  const transcript = collectCurrentRoundTranscript(sessionKey);
  const statusSnapshot = collectDemandStatusSnapshot();
  if (!transcript.trim()) {
    renderDemandRows(buildDemandRowsPreservingStatus(statusSnapshot), "本轮还没有转写内容，诉求对照保持待采集状态。");
    toast("本轮还没有转写内容，暂不生成诉求");
    return;
  }

  const side = demandSide(sessionKey);
  let extractionCompleted = false;
  try {
    setDemandExtracting(true, "诉求提取中");
    $("#decision-next-text").textContent = "正在根据本轮会谈提取诉求...";
    const extractStartedAt = performance.now();
    const extractionResp = await fetch("/api/ai/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_key: sessionKey,
        transcript,
        current_extraction: latestExtraction(sessionKey) || {},
        current_demand: side ? appState.demandStore[side] || {} : {},
      }),
    });
    const extractionPayload = await readJsonResponse(extractionResp, "诉求提取接口失败");
    console.info("诉求提取耗时", Math.round(performance.now() - extractStartedAt), "ms", extractionPayload._meta || {});
    runtime.extraction = extractionPayload;
    extractionCompleted = true;
    appState.analysisDirty = true;
    updateDemandStore(sessionKey, runtime.extraction);
    archiveCurrentRound(sessionKey);
    setDemandExtracting(false);
    renderPendingAnalysisDemand("诉求已根据本轮会谈更新，等待一致性分析。", statusSnapshot);
    await waitNextPaint();

    const aExtract = latestExtraction("a");
    const bExtract = latestExtraction("b");
    if (aExtract && bExtract) {
      setAnalysisAnalyzing(true, "一致性分析中");
      $("#decision-next-text").textContent = "诉求已提取，正在进行一致性判断...";
      const compareScript = $("#compare-joint-script");
      if (compareScript) compareScript.textContent = "诉求已回显更新，正在进行一致性判断...";
      try {
        const analyzeStartedAt = performance.now();
        const analysisResp = await fetch("/api/ai/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_a: aExtract,
            session_b: bExtract,
            demand_rows: buildDemandRowsFromStore(),
            demand_a: appState.demandStore.a,
            demand_b: appState.demandStore.b,
          }),
        });
        appState.currentAnalysis = await readJsonResponse(analysisResp, "一致性判断接口失败");
        console.info("一致性分析耗时", Math.round(performance.now() - analyzeStartedAt), "ms", appState.currentAnalysis._meta || {});
        appState.analysisDirty = false;
        renderAnalysisDemand(appState.currentAnalysis);
      } finally {
        setAnalysisAnalyzing(false);
      }
    } else {
      renderDemandRows(buildDemandRowsPreservingStatus(statusSnapshot), "已根据本轮会谈更新当前人员诉求，等待另一方会谈后形成对照。");
    }
    toast("已根据本轮对话生成诉求");
  } catch (error) {
    if (extractionCompleted) {
      appState.analysisDirty = true;
      renderDemandRows(buildDemandRowsPreservingStatus(statusSnapshot), "一致性分析失败，已先保留并回显本轮诉求。");
    } else {
      renderDemandRows(buildDemandRowsPreservingStatus(statusSnapshot), "诉求提取失败，已保留当前诉求表。");
    }
    toast(error.message || "诉求提取失败，请稍后重试");
  } finally {
    setAnalysisAnalyzing(false);
    setDemandExtracting(false);
  }
}

function setWorkflow(index) {
  // 页面已按原型收敛为单工作台，流程节点不再渲染，只保留当前流程值给后续逻辑使用。
  appState.workflowIndex = index;
}

function renderVoiceSummary(sessionKey) {
  const session = sessions[sessionKey];
  const state = appState.roundState[sessionKey];
  const party = getPartyBySession(sessionKey);

  $("#party-name").textContent = party?.name || session.speaker || "/";
  $("#party-gender").textContent = party?.gender || "/";
  $("#party-id").textContent = party?.id_no || "/";
  $("#party-identity").textContent = party?.identity || party?.role || "/";
  $("#speaker-state-main").textContent = "说话人分离：已关闭";

  $("#round-current").textContent = `${session.roundLabel} · ${state.running ? "录音中" : (state.stopped ? "已结束" : "待开始")}`;
  const displaySeconds = state.stopped && state.stoppedSeconds !== null ? state.stoppedSeconds : state.seconds;
  $("#session-timer").textContent = formatTime(displaySeconds);
  $("#record-status").textContent = state.stopped ? "已结束" : (state.running ? "录音中" : "待开始");
  $("#record-toggle-label").textContent = state.running ? "暂停" : (state.stopped ? "开始新轮" : "开始本轮");
  const recordButton = $('[data-control="pause"]');
  if (recordButton) {
    recordButton.classList.toggle("active", state.running);
    recordButton.setAttribute("aria-label", state.running ? "暂停本轮" : "开始本轮");
    recordButton.innerHTML = state.running
      ? '<i data-lucide="pause"></i><span id="record-toggle-label">暂停</span>'
      : `<i data-lucide="play"></i><span id="record-toggle-label">${state.stopped ? "开始新轮" : "开始本轮"}</span>`;
  }
  if (!state.running) {
    updateAudioMeter(0, false);
    const audioText = $("#audio-level-text");
    if (audioText) audioText.textContent = state.stopped ? "收音已停止" : "收音待开始";
  }
  renderRuntimeUtterances(sessionKey);
  const runtime = currentRuntime(sessionKey);
  if (appState.currentAnalysis && !appState.analysisDirty) {
    renderAnalysisDemand(appState.currentAnalysis);
  } else if (runtime.extraction) {
    renderSingleSideDemand(sessionKey, runtime.extraction);
  } else if (hasDemandStoreContent()) {
    renderDemandRows(buildDemandRowsPreservingStatus(), "已保留前序诉求，等待本轮停止后继续更新。");
  } else if (!currentRuntime("a").extraction && !currentRuntime("b").extraction) {
    clearDemandCompare();
  }
}

function renderComparePage(mode) {
  if (appState.currentAnalysis && !appState.analysisDirty) {
    renderAnalysisDemand(appState.currentAnalysis);
    return;
  }
  const aExtraction = latestExtraction("a");
  const bExtraction = latestExtraction("b");
  if (!aExtraction && !bExtraction) {
    if (hasDemandStoreContent()) {
      renderDemandRows(buildDemandRowsPreservingStatus(), "已保留前序诉求，等待本轮停止后继续更新。");
      return;
    }
    clearDemandCompare();
    return;
  }
  if (aExtraction && bExtraction) {
    updateDemandStore("a", aExtraction);
    updateDemandStore("b", bExtraction);
    renderDemandRows(buildDemandRowsPreservingStatus(), "双方诉求已采集，等待重新生成一致性对照。");
    return;
  }
  if (aExtraction) {
    renderSingleSideDemand("a", aExtraction);
    return;
  }
  if (bExtraction) {
    renderSingleSideDemand("b", bExtraction);
    return;
  }
  clearDemandCompare();
}

function resetDocumentState(key = appState.currentDocument) {
  appState.currentDocument = key;
  $("#document-paper").contentEditable = "false";
  $("#document-paper").classList.remove("editing");
  const editButton = $(".js-edit-document span");
  if (editButton) editButton.textContent = "编辑文本";
  $$(".sign-box").forEach((box) => {
    box.classList.remove("signed");
    const label = $("b", box);
    if (label) label.textContent = box.dataset.signer.includes("确认") ? "待确认" : "未签";
  });
  $("#sign-status").textContent = "待签署";
  $("#sign-status").className = "tag amber";
  $("#archive-ai-status").textContent = "待完成";
  $("#archive-ai-status").className = "tag amber hidden-status";
}

function setPage(page) {
  appState.currentPage = page;
  document.body.dataset.page = page;
  document.body.classList.toggle("document-open", page === "document");
  $$(".page").forEach((section) => section.classList.toggle("active", section.id === `page-${page}`));
  if (page === "voice") {
    renderVoiceSummary(appState.currentSession);
  }
  if (window.lucide) window.lucide.createIcons();
}

function updateSession(sessionKey) {
  appState.currentSession = sessionKey;
  $$(".segment").forEach((button) => button.classList.toggle("active", button.dataset.session === sessionKey));
  renderVoiceSummary(sessionKey);
  if (window.lucide) window.lucide.createIcons();
}

function openSignature(box) {
  appState.currentSigner = box;
  $("#signature-title").textContent = `采集${box.dataset.signer}`;
  $("#signature-modal").classList.add("show");
  $("#signature-modal").setAttribute("aria-hidden", "false");
  clearSignaturePad();
}

function closeSignature() {
  $("#signature-modal").classList.remove("show");
  $("#signature-modal").setAttribute("aria-hidden", "true");
  appState.currentSigner = null;
}

function clearSignaturePad() {
  const canvas = $("#signature-pad");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#fbfcfc";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#d8e0e3";
  ctx.setLineDash([6, 6]);
  ctx.beginPath();
  ctx.moveTo(24, 160);
  ctx.lineTo(canvas.width - 24, 160);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#607077";
  ctx.font = "16px Microsoft YaHei";
  ctx.fillText("请在此区域签名", 24, 40);
}

async function toggleCurrentRound() {
  const state = appState.roundState[appState.currentSession];
  const session = sessions[appState.currentSession];
  if (appState.extractingDemand) {
    toast("诉求提取中，请稍候");
    return;
  }
  if (state.running) {
    state.running = false;
    await stopRealtimeAsr(false);
    toast("本轮已暂停，点击停止后生成诉求");
  } else {
    if (state.stopped) {
      const statusSnapshot = collectDemandStatusSnapshot();
      state.index += 1;
      state.seconds = 0;
      state.stoppedSeconds = null;
      resetRuntime(appState.currentSession);
      // 同一当事人进入新一轮时，旧一致性结论先标记为待刷新；状态列保持当前展示，等新分析完成后统一更新。
      appState.analysisDirty = true;
      renderDemandRows(buildDemandRowsPreservingStatus(statusSnapshot), "已保留前序诉求，等待本轮停止后继续更新。");
    }
    if (!state.startedAt || state.stopped) {
      state.startedAt = Date.now();
      state.endedAt = null;
    }
    state.running = true;
    state.stopped = false;
    session.roundLabel = buildRoundLabel(appState.currentSession, state.startedAt);
    renderVoiceSummary(appState.currentSession);
    try {
      await startRealtimeAsr();
      toast(`已开始第 ${state.index} 轮实时语音转写`);
    } catch (error) {
      state.running = false;
      state.stopped = false;
      renderVoiceSummary(appState.currentSession);
      toast(`实时转写启动失败：${error.message || "请检查麦克风和 ASR 配置"}`);
    }
  }
  renderVoiceSummary(appState.currentSession);
}

async function stopCurrentRound() {
  const state = appState.roundState[appState.currentSession];
  const stoppedSession = appState.currentSession;
  const stoppedRoundIndex = state.index;
  if (appState.extractingDemand) {
    toast("诉求提取中，请稍候");
    return;
  }
  if (!state.running && !state.startedAt) {
    toast("本轮还未开始");
    return;
  }
  if (state.stopped) {
    toast("本轮已停止，正在等待或查看诉求结果");
    return;
  }
  state.running = false;
  state.stopped = true;
  state.stoppedSeconds = state.seconds;
  state.endedAt = Date.now();
  await stopRealtimeAsr(true);
  archiveCurrentRound(appState.currentSession);
  renderVoiceSummary(appState.currentSession);
  await generateDemandAfterRound(appState.currentSession);
  await advanceSimulationSession(stoppedSession, stoppedRoundIndex);
}

function bindEvents() {
  $$(".segment").forEach((button) => {
    button.addEventListener("click", () => {
      updateSession(button.dataset.session);
      toast(`已切换到${button.textContent.trim()}会谈`);
    });
  });

  const pauseButton = $('[data-control="pause"]');
  const stopButton = $('[data-control="stop"]');
  if (pauseButton) pauseButton.addEventListener("click", toggleCurrentRound);
  if (stopButton) stopButton.addEventListener("click", stopCurrentRound);

  $$(".js-supplement").forEach((button) => button.addEventListener("click", () => {
    setPage("voice");
    toast("已返回会谈页面");
  }));

  $$(".js-back-voice").forEach((button) => button.addEventListener("click", () => {
    setPage("voice");
    toast("已返回会谈页面");
  }));

  $$(".js-joint").forEach((button) => button.addEventListener("click", async () => {
    setPage("document");
    setWorkflow(2);
    try {
      await generateDocumentDraft("agreement");
      toast("已根据会谈内容生成协议草案");
    } catch (error) {
      renderAgreementDraft({}, await getCaseState());
      toast(error.message || "协议生成失败，已生成基础模板");
    }
  }));

  $$(".js-failed-record").forEach((button) => button.addEventListener("click", async () => {
    setPage("document");
    setWorkflow(2);
    try {
      await generateDocumentDraft("record");
      if (hasBothPartyDemands(collectDemandRowsForDraft()) && collectRecordRounds().length) {
        toast("已根据会谈内容生成调解笔录");
      }
    } catch (error) {
      renderRecordDraft({}, await getCaseState(), collectRecordRounds());
      toast(error.message || "笔录生成失败，已生成基础模板");
    }
  }));

  $(".js-redraft").addEventListener("click", async () => {
    try {
      await generateDocumentDraft(appState.currentDocument);
      toast("已基于最新诉求重新生成当前文书");
    } catch (error) {
      toast(error.message || "重新生成失败，请稍后重试");
    }
  });

  $(".js-edit-document").addEventListener("click", (event) => {
    const paper = $("#document-paper");
    const editing = paper.contentEditable !== "true";
    paper.contentEditable = String(editing);
    paper.classList.toggle("editing", editing);
    event.currentTarget.querySelector("span").textContent = editing ? "完成编辑" : "编辑文本";
    if (editing) paper.focus();
    toast(editing ? "文书正文已进入可编辑状态" : "文书正文编辑已保存");
  });

  $$(".sign-box").forEach((box) => {
    box.addEventListener("click", () => openSignature(box));
    box.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") openSignature(box);
    });
  });

  $$(".modal-backdrop, [data-close-modal]").forEach((node) => node.addEventListener("click", closeSignature));
  $("#clear-signature").addEventListener("click", clearSignaturePad);
  $("#save-signature").addEventListener("click", () => {
    if (!appState.currentSigner) return;
    const signerName = appState.currentSigner.dataset.signer;
    appState.currentSigner.classList.add("signed");
    const label = $("b", appState.currentSigner);
    if (label) label.textContent = "已签";
    closeSignature();
    const allSigned = $$(".sign-box").every((box) => box.classList.contains("signed"));
    if (allSigned) {
      $("#sign-status").textContent = "已归档";
      $("#sign-status").className = "tag green";
    } else {
      $("#sign-status").textContent = "待签署";
      $("#sign-status").className = "tag amber";
    }
    toast(`${signerName}已采集`);
  });

  $(".js-complete-sign").addEventListener("click", () => {
    const pendingSign = $$(".sign-box").some((box) => !box.classList.contains("signed"));
    if (pendingSign) {
      toast("李江、周枫和民警签名未采集完整");
      return;
    }
    setWorkflow(3);
    $("#sign-status").textContent = "已归档";
    $("#sign-status").className = "tag green";
    $("#archive-ai-status").textContent = "核验通过";
    $("#archive-ai-status").className = "tag green hidden-status";
    toast("签署完成，案件已进入归档状态");
  });

  $$(".js-print").forEach((button) => button.addEventListener("click", () => toast("已发送到打印队列")));

  setInterval(() => {
    const state = appState.roundState[appState.currentSession];
    if (!state.running || state.stopped) return;
    state.seconds += 1;
    if (appState.currentPage === "voice") {
      $("#session-timer").textContent = formatTime(state.seconds);
    }
  }, 1000);
}

async function init() {
  await getCaseState();
  applyCasePartiesToUi();
  renderAgreementDraft({}, appState.caseState || {});
  clearSignaturePad();
  bindEvents();
  renderVoiceSummary("a");
  renderComparePage("b");
  resetDocumentState("agreement");
  setPage("voice");
  setWorkflow(1);
  if (window.lucide) window.lucide.createIcons();
}

document.addEventListener("DOMContentLoaded", init);
