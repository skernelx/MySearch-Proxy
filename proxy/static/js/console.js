
function showToast(message, type = 'info') {
  const root = document.getElementById('toast-root');
  if (!root) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  root.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('toast-fade-out');
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}
const STORAGE_KEY = 'multi_service_proxy_pwd';
const LEGACY_STORAGE_KEY = 'tavily_proxy_pwd';
const ACTIVE_SERVICE_KEY = 'multi_service_proxy_active_service';
const API = '';
const SERVICE_META = {
  tavily: {
    label: 'Tavily',
    emailPrefix: 'tavily-',
    tokenPrefix: 'tvly-',
    keyPlaceholder: 'tvly-xxxxxxxx',
    importPlaceholder: '支持粘贴 email,password,tvly-xxx,timestamp 或仅 tvly-xxx，每行一条',
    quotaSource: '真实额度来自 Tavily 官方 GET /usage',
    routeHint: '代理端点: POST /api/search, POST /api/extract',
    syncButton: '同步 Tavily 额度',
    syncSupported: true,
    panelIntro: '适合新闻、网页线索和基础搜索入口，继续保留现有 Tavily 工作台逻辑不动。',
    tokenPoolDesc: '给业务侧发放 Tavily 代理 Token，和 Exa / Firecrawl 完全分开创建、限流、统计。',
    keyPoolDesc: 'Tavily Key 独立存储，导入时只写入 Tavily 池，不会和 Exa 或 Firecrawl 混用。',
    switcherBadges: ['Search', '网页发现', '官方额度同步'],
    switcherFoot: '独立 Key 池 + 独立额度同步',
    spotlightDesc: 'Tavily 继续负责第一层网页发现，这一栏保留现有功能与额度同步逻辑。',
  },
  exa: {
    label: 'Exa',
    emailPrefix: 'exa-',
    tokenPrefix: 'exat-',
    keyPlaceholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
    importPlaceholder: '支持粘贴 email,password,uuid,timestamp 或仅 UUID Key，每行一条',
    quotaSource: 'Exa 实时额度暂时无法查询，控制台当前只统计代理调用',
    routeHint: '代理端点: POST /exa/search',
    syncButton: 'Exa 暂不支持同步',
    syncSupported: false,
    panelIntro: '适合补充网页发现入口，已经独立成 Exa 工作台、Exa Key 池和 Exa Token 池。',
    tokenPoolDesc: '给业务侧发放 Exa 代理 Token，和 Tavily / Firecrawl 完全分开创建、限流、统计。',
    keyPoolDesc: 'Exa Key 独立存储，支持直接导入 UUID key，不和别的服务共用池子。',
    switcherBadges: ['Search', '网页发现', '代理统计'],
    switcherFoot: '独立 Key 池 + 独立代理统计',
    spotlightDesc: 'Exa 已经收成单独工作台，现在可以单独导入 Key、签发 Token，并通过 /exa/search 直接代理搜索。',
  },
  firecrawl: {
    label: 'Firecrawl',
    emailPrefix: 'fc-',
    tokenPrefix: 'fctk-',
    keyPlaceholder: 'fc-xxxxxxxx',
    importPlaceholder: '支持粘贴 email,password,fc-xxx,timestamp 或仅 fc-xxx，每行一条',
    quotaSource: '真实额度来自 Firecrawl /v2/team/credit-usage',
    routeHint: '代理端点: /firecrawl/*，例如 POST /firecrawl/v2/scrape',
    syncButton: '同步 Firecrawl 额度',
    syncSupported: true,
    panelIntro: '适合正文抓取、文档页、PDF 和结构化抽取，继续保持独立 Firecrawl 工作台。',
    tokenPoolDesc: '给业务侧发放 Firecrawl 代理 Token，和 Tavily / Exa 完全分开创建、限流、统计。',
    keyPoolDesc: 'Firecrawl Key 独立存储，导入时只写入 Firecrawl 池，不会和其他服务混用。',
    switcherBadges: ['Depth', '正文抓取', '官方额度同步'],
    switcherFoot: '独立 Key 池 + 独立额度同步',
    spotlightDesc: 'Firecrawl 继续负责正文抓取与页面抽取，额度同步仍按 Firecrawl credits 展示。',
  },
};

const WORKSPACE_META = {
  ...SERVICE_META,
  social: {
    label: 'Social / X',
    emailPrefix: 'X search',
    tokenPrefix: 'shared auth',
    routeHint: '代理端点: POST /social/search',
    quotaSource: 'grok2api / xAI-compatible social router',
    switcherBadges: ['X Search', 'compatible', '自动继承'],
    switcherFoot: '统一 Social 路由 + 统一输出结构',
    spotlightDesc: 'Social / X 工作台负责舆情路由和 token 池映射，对外统一暴露 /social/search。',
  },
};

let PWD = localStorage.getItem(STORAGE_KEY) || localStorage.getItem(LEGACY_STORAGE_KEY) || '';
let activeService = localStorage.getItem(ACTIVE_SERVICE_KEY) || 'tavily';
let latestServices = {};
let latestSocial = {};
let latestMySearch = {};
let latestSettings = {};

function clearStoredPasswords() {
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(LEGACY_STORAGE_KEY);
}

function setLoginBusy(isBusy) {
  const input = document.getElementById('pwd-input');
  const button = document.getElementById('login-submit');
  if (input) input.disabled = isBusy;
  if (button) {
    button.disabled = isBusy;
    button.textContent = isBusy ? '登录中...' : '进入控制台';
  }
}

function showDashboard() {
  document.getElementById('login-err').classList.add('hidden');
  document.getElementById('login-box').classList.add('hidden');
  document.getElementById('dashboard').classList.remove('hidden');
}

function showLogin() {
  document.getElementById('dashboard').classList.add('hidden');
  document.getElementById('login-box').classList.remove('hidden');
  setLoginBusy(false);
}

async function fetchSession(method, path, body) {
  const options = {
    method,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
    },
  };
  if (body !== undefined) {
    options.body = JSON.stringify(body);
  }
  const response = await fetch(API + path, options);
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = text ? { detail: text } : {};
  }
  if (!response.ok) {
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  return payload;
}

async function loginWithPassword(password) {
  await fetchSession('POST', '/api/session/login', { password });
}

async function hasServerSession() {
  try {
    await fetchSession('GET', '/api/session');
    return true;
  } catch {
    return false;
  }
}

async function migrateStoredPasswordIfNeeded() {
  if (!PWD) return false;
  try {
    await loginWithPassword(PWD);
    PWD = '';
    clearStoredPasswords();
    return true;
  } catch {
    PWD = '';
    clearStoredPasswords();
    return false;
  }
}

function socialModeLabel(mode) {
  if (mode === 'admin-auto') return '后台自动继承';
  if (mode === 'hybrid') return '后台继承 + 手动覆写';
  return '手动模式';
}

function socialTokenSourceLabel(source) {
  if (source === 'grok2api app.api_key') return '后台自动继承';
  if (source === 'SOCIAL_GATEWAY_UPSTREAM_API_KEY') return '手动上游 API key';
  if (source === 'manual SOCIAL_GATEWAY_TOKEN') return '手动客户端 token';
  return '尚未配置';
}

function socialStatusLabel(social) {
  if (social?.admin_connected) return '后台已接通';
  if (social?.upstream_key_configured) return '已可转发搜索';
  return '等待配置';
}

function buildSocialProxyEnv(social) {
  const baseUrl = social.upstream_base_url || 'https://media.example.com/v1';
  const adminBaseUrl = social.admin_base_url || baseUrl.replace(/\/v1$/, '');
  return `# 推荐：只填 grok2api 后台地址和后台 app_key，proxy 会自动继承上游凭证与 token 池
SOCIAL_GATEWAY_UPSTREAM_BASE_URL=${baseUrl}
SOCIAL_GATEWAY_ADMIN_BASE_URL=${adminBaseUrl}
SOCIAL_GATEWAY_ADMIN_APP_KEY=YOUR_GROK2API_APP_KEY
SOCIAL_GATEWAY_MODEL=grok-4.1-fast

# 可选：只有你想覆写默认行为时才需要
# SOCIAL_GATEWAY_UPSTREAM_API_KEY=
# SOCIAL_GATEWAY_TOKEN=`;
}

function buildSocialMySearchEnv(social) {
  const baseUrl = location.origin;
  const socialReady = social?.admin_connected || social?.upstream_key_configured;
  return `# 推荐：直接用 MySearch 通用 token，一次接上 Tavily / Firecrawl / Exa / Social
MYSEARCH_PROXY_BASE_URL=${baseUrl}
MYSEARCH_PROXY_API_KEY=YOUR_MYSEARCH_PROXY_TOKEN

# 当前 Social / X ${socialReady ? '已经接通，可直接复用上面的通用 token。' : '还没完全接通；上面的通用 token 先可用于 Tavily / Firecrawl / Exa。'}

# 如果你只想单独接 Social / X，也可以显式写 compatible 模式：
MYSEARCH_XAI_SEARCH_MODE=compatible
MYSEARCH_XAI_SOCIAL_BASE_URL=${baseUrl}
MYSEARCH_XAI_SOCIAL_SEARCH_PATH=/social/search
MYSEARCH_XAI_API_KEY=YOUR_MYSEARCH_PROXY_TOKEN`;
}

function buildMySearchEnv(mysearch, social) {
  const baseUrl = location.origin;
  const token = mysearch?.tokens?.[0]?.token || 'YOUR_MYSEARCH_PROXY_TOKEN';
  const socialReady = social?.admin_connected || social?.upstream_key_configured;
  return `# 最省事的接法：只填这两项，MySearch 会默认走当前 proxy
MYSEARCH_PROXY_BASE_URL=${baseUrl}
MYSEARCH_PROXY_API_KEY=${token}

# 说明：
# - 这一个 token 会同时允许 Tavily / Firecrawl / Exa${socialReady ? ' / Social' : ''}
# - Social / X ${socialReady ? '当前已接通，会默认复用同一个 token' : '当前还没完全接通，后续接好后也会自动复用同一个 token'}

# 可选：如果你想把 MCP 额外暴露成远程 HTTP，再补这一段
# MYSEARCH_MCP_HOST=0.0.0.0
# MYSEARCH_MCP_PORT=8000
# MYSEARCH_MCP_STREAMABLE_HTTP_PATH=/mcp`;
}

function buildMySearchInstall() {
  return `git clone https://github.com/skernelx/MySearch-Proxy.git
cd MySearch-Proxy
cp mysearch/.env.example mysearch/.env

# 把上面的 MYSEARCH_PROXY_* 粘进去后执行
./install.sh

# 如果你想作为远程 MCP 提供给别的客户端：
./venv/bin/python -m mysearch \\
  --transport streamable-http \\
  --host 0.0.0.0 \\
  --port 8000 \\
  --streamable-http-path /mcp`;
}

function renderSocialBoard(social) {
  const stats = social?.stats || {};
  const mode = socialModeLabel(social?.mode || 'manual');
  const statusText = socialStatusLabel(social);
  const tokenSource = socialTokenSourceLabel(social?.token_source || '');
  const authText = social?.client_auth_configured ? '已允许客户端调用 /social/search' : '还没有设置客户端 token';
  const videoValue = stats.video_remaining === null
    ? '<div class="value muted is-text">无法统计</div>'
    : `<div class="value muted">${fmtNum(stats.video_remaining)}</div>`;
  let foot = '现在还没有连上 Social / X 上游。补齐 grok2api 后台地址和 app key，或者手动填写上游 key 后，这里会开始显示完整状态。';
  if (social?.admin_connected) {
    foot = '当前通过 grok2api 后台自动同步 token 状态和剩余额度。对外仍然统一提供 MySearch 的 /social/search 结果结构。';
  } else if (social?.upstream_key_configured) {
    foot = '当前已经能转发 Social 搜索，但还没有连上后台统计。补上 grok2api app key 后，这里会显示完整 token 状态。';
  }
  const errorLine = social?.error
    ? `<div class="social-board-foot is-error">最近错误：${escapeHtml(social.error)}</div>`
    : '';

  document.getElementById('social-board').innerHTML = `
    <div class="social-board-top">
      <div>
        <div class="social-board-kicker">Social / X 状态</div>
        <div class="social-board-title">Social / X Router</div>
      </div>
    </div>
    <div class="social-board-desc">
      这里看的是 MySearch 的 Social / X 路由运行面。底层可以接 grok2api，也可以兼容别的 xAI-compatible 上游，但对外始终是一套统一结果结构。
    </div>
    <div class="social-board-grid">
      <div class="social-metric">
        <div class="label">Token 总数</div>
        <div class="value">${fmtNum(stats.token_total || 0)}</div>
      </div>
      <div class="social-metric">
        <div class="label">Token 正常</div>
        <div class="value ok">${fmtNum(stats.token_normal || 0)}</div>
      </div>
      <div class="social-metric">
        <div class="label">Token 限流</div>
        <div class="value warn">${fmtNum(stats.token_limited || 0)}</div>
      </div>
      <div class="social-metric">
        <div class="label">Token 失效</div>
        <div class="value danger">${fmtNum(stats.token_invalid || 0)}</div>
      </div>
      <div class="social-metric">
        <div class="label">Chat 剩余</div>
        <div class="value">${fmtNum(stats.chat_remaining || 0)}</div>
      </div>
      <div class="social-metric">
        <div class="label">Image 剩余</div>
        <div class="value info">${fmtNum(stats.image_remaining || 0)}</div>
      </div>
      <div class="social-metric">
        <div class="label">Video 剩余</div>
        ${videoValue}
      </div>
      <div class="social-metric">
        <div class="label">总调用次数</div>
        <div class="value">${fmtNum(stats.total_calls || 0)}</div>
      </div>
    </div>
    <div class="social-board-summary">
      <div class="social-board-summary-item">
        <div class="label">当前状态</div>
        <div class="value">${escapeHtml(statusText)}</div>
      </div>
      <div class="social-board-summary-item">
        <div class="label">Token 来源</div>
        <div class="value">${escapeHtml(tokenSource)}</div>
      </div>
      <div class="social-board-summary-item">
        <div class="label">客户端访问</div>
        <div class="value">${escapeHtml(authText)}</div>
      </div>
      <div class="social-board-summary-item">
        <div class="label">工作模式</div>
        <div class="value">${escapeHtml(mode)}</div>
      </div>
    </div>
    <div class="social-board-foot">
      <strong>${statusText}</strong> ${foot}
    </div>
    ${errorLine}
  `;
}

function renderSocialIntegration(social) {
  const mode = socialModeLabel(social?.mode || 'manual');
  const source = socialTokenSourceLabel(social?.token_source || '');
  const proxyConfigured = Boolean(social?.upstream_key_configured);
  const clientConfigured = Boolean(social?.client_auth_configured);
  const authLabel = clientConfigured ? '已就绪' : '未配置';
  const upstreamBase = social?.upstream_base_url || 'https://media.example.com/v1';
  const adminBase = social?.admin_base_url || '未设置';
  let note = '推荐只填写 grok2api 后台地址和 app key，让 proxy 自动继承上游密钥和 token 池。';
  if (social?.admin_connected) {
    note = '当前已经走后台自动继承，后面通常不需要再手动维护上游 key 和客户端 token。';
  } else if (social?.upstream_key_configured) {
    note = '当前已经可以调用，但还没有接上后台 token 面板；如果你想看到完整统计，补上 grok2api app key 即可。';
  }
  const noteClass = social?.error ? 'integration-note is-error' : 'integration-note';

  document.getElementById('social-integration').innerHTML = `
    <h3>Social / X 接入</h3>
    <p class="desc">推荐优先接 grok2api 后台。这样只要填后台地址和 app key，proxy 就能自动继承上游密钥与 token 池，不需要再把配置拆成很多手动变量。</p>
    <div class="integration-summary">
      <div class="integration-summary-item">
        <div class="label">工作模式</div>
        <div class="value">${escapeHtml(mode)}</div>
      </div>
      <div class="integration-summary-item">
        <div class="label">上游接口</div>
        <div class="value mono">${escapeHtml(upstreamBase)}</div>
      </div>
      <div class="integration-summary-item">
        <div class="label">后台地址</div>
        <div class="value mono">${escapeHtml(adminBase)}</div>
      </div>
      <div class="integration-summary-item">
        <div class="label">Token 来源</div>
        <div class="value">${escapeHtml(source)}</div>
      </div>
      <div class="integration-summary-item">
        <div class="label">客户端鉴权</div>
        <div class="value">${escapeHtml(authLabel)}</div>
      </div>
      <div class="integration-summary-item">
        <div class="label">接入结果</div>
        <div class="value">${proxyConfigured ? '已拿到可用上游 key' : '尚未拿到上游 key'}</div>
      </div>
    </div>
    <div class="${noteClass}">
      <strong>${socialStatusLabel(social)}</strong> ${escapeHtml(note)}
      ${social?.error ? `<br>最近错误：${escapeHtml(social.error)}` : ''}
    </div>
    <div class="code-toolbar">
      <div class="endpoint">Proxy 端环境变量。通常只要复制这一段，再补你自己的 grok2api app key。</div>
      <button class="btn btn-sm" onclick="copyCode('social-proxy-env', this)">复制 Proxy 配置</button>
    </div>
    <pre class="code-block mono" id="social-proxy-env"></pre>
    <div class="code-toolbar" style="margin-top:12px">
      <div class="endpoint">MySearch / MCP / Skill 端环境变量。现在更推荐直接使用 MySearch 通用 token，一次接上全部路由。</div>
      <button class="btn btn-sm" onclick="copyCode('social-mysearch-env', this)">复制 MySearch 配置</button>
    </div>
    <pre class="code-block mono" id="social-mysearch-env"></pre>
  `;

  document.getElementById('social-proxy-env').textContent = buildSocialProxyEnv(social || {});
  document.getElementById('social-mysearch-env').textContent = buildSocialMySearchEnv(social || {});
}

function renderMySearchQuickstart(mysearch, social) {
  const root = document.getElementById('mysearch-quickstart');
  if (!root) return;

  const tokens = mysearch?.tokens || [];
  const tokenCount = tokens.length;
  const todayCount = mysearch?.overview?.today_count || 0;
  const monthCount = mysearch?.overview?.month_count || 0;
  const socialReady = Boolean(social?.admin_connected || social?.upstream_key_configured);
  const noteClass = tokenCount > 0 ? 'integration-note' : 'integration-note is-error';
  const note = tokenCount > 0
    ? '这里创建的是 MySearch 通用 token。它会同时被 Tavily / Firecrawl / Exa 路由接受，并且在 Social / X 已接通时也可以直接复用。'
    : '先创建一个 MySearch 通用 token。创建后控制台会自动生成可直接复制的 .env 配置。';

  root.innerHTML = `
    <div class="service-head">
      <div>
        <span class="service-chip">MySearch MCP</span>
        <h2>MySearch 快速接入</h2>
        <p>这块不是 provider 池，而是给 Codex / Claude Code / 其他 MCP 客户端准备的统一接入层。目标就是让用户少填变量、少记路径、少区分底层服务。</p>
      </div>
      <div class="service-tools">
        <div class="service-sync-meta">推荐直接复制下面的 <span class="mono">MYSEARCH_PROXY_*</span> 配置，不再手写一堆 provider 地址。</div>
        <div class="service-sync-meta">通用 Token 前缀：<span class="mono">${escapeHtml(mysearch?.token_prefix || 'mysp-')}</span></div>
      </div>
    </div>
    <div class="service-body">
      <div class="section-grid">
        <div class="subcard api-shell">
          <h3>一键配置</h3>
          <p class="desc">推荐把 MySearch 统一接到当前 proxy。这样客户端只认一个 base URL 和一个通用 token，底层 Tavily / Firecrawl / Exa / Social 都由 proxy 负责收口。</p>
          <div class="integration-summary">
            <div class="integration-summary-item">
              <div class="label">Proxy Base URL</div>
              <div class="value mono">${escapeHtml(location.origin)}</div>
            </div>
            <div class="integration-summary-item">
              <div class="label">通用 Token</div>
              <div class="value">${fmtNum(tokenCount)}</div>
            </div>
            <div class="integration-summary-item">
              <div class="label">今日总调用</div>
              <div class="value">${fmtNum(todayCount)}</div>
            </div>
            <div class="integration-summary-item">
              <div class="label">本月总调用</div>
              <div class="value">${fmtNum(monthCount)}</div>
            </div>
            <div class="integration-summary-item">
              <div class="label">Social / X</div>
              <div class="value">${socialReady ? '已接通' : '待接通'}</div>
            </div>
            <div class="integration-summary-item">
              <div class="label">默认安装形态</div>
              <div class="value">stdio</div>
            </div>
          </div>
          <div class="${noteClass}">
            <strong>${tokenCount > 0 ? '可以直接复制配置了' : '还差一个通用 token'}</strong> ${escapeHtml(note)}
          </div>
          <div class="code-toolbar">
            <div class="endpoint">复制到 <span class="mono">mysearch/.env</span> 就能用。默认已经包含统一 proxy 接法。</div>
            <button class="btn btn-sm" onclick="copyCode('mysearch-proxy-env', this)">复制 .env</button>
          </div>
          <pre class="code-block mono" id="mysearch-proxy-env"></pre>
          <div class="code-toolbar" style="margin-top:12px">
            <div class="endpoint">本地安装 / 远程启动命令，按仓库默认流程直接走。</div>
            <button class="btn btn-sm" onclick="copyCode('mysearch-install-cmd', this)">复制命令</button>
          </div>
          <pre class="code-block mono" id="mysearch-install-cmd"></pre>
        </div>
        <details class="subcard"><summary style="cursor:pointer;font-weight:600;font-size:18px;margin-bottom:8px;outline:none">MySearch 通用 Token</summary>
          <p class="desc">这个 token 专门给上层 MCP / Skill 用。和 Tavily / Firecrawl / Exa 各自的服务 token 分开管理，但调用时会被三条 provider 路由一起接受。</p>
          <div class="form-row">
            <input class="input-grow" type="text" id="token-name-mysearch" placeholder="可选备注，例如 Codex / Claude Code / Team Demo">
            <button class="btn btn-primary" onclick="createToken('mysearch')">创建 MySearch Token</button>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Token</th>
                  <th>名称</th>
                  <th>额度</th>
                  <th>代理统计</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody id="tokens-body-mysearch"></tbody>
            </table>
          </div>
        </details>
      </div>
    </div>
  `;

  document.getElementById('mysearch-proxy-env').textContent = buildMySearchEnv(mysearch || {}, social || {});
  document.getElementById('mysearch-install-cmd').textContent = buildMySearchInstall();
  renderTokens('mysearch', tokens);
}

function headers() {
  const base = {
    'Content-Type': 'application/json',
  };
  if (PWD) {
    base['X-Admin-Password'] = PWD;
  }
  return base;
}

async function api(method, path, body) {
  const options = { method, headers: headers(), credentials: 'same-origin' };
  if (body !== undefined) {
    options.body = JSON.stringify(body);
  }

  const response = await fetch(API + path, options);
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = text ? { detail: text } : {};
  }

  if (response.status === 401) {
    logout();
    throw new Error('Unauthorized');
  }

  if (!response.ok) {
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }

  return payload;
}

function setStatus(id, message, isError = false) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!message) {
    el.textContent = '';
    el.classList.add('hidden');
    el.classList.remove('is-error');
    return;
  }
  el.textContent = message;
  el.classList.remove('hidden');
  el.classList.toggle('is-error', Boolean(isError));
}

function describeConfiguredSecret(masked, configured) {
  if (!configured) return '当前未配置。';
  return `当前已配置 ${masked || 'secret'}，留空表示保持不变。`;
}

function fillSettingsForm(settings) {
  const social = settings?.social || {};
  document.getElementById('settings-social-upstream-base-url').value = social.upstream_base_url || '';
  document.getElementById('settings-social-upstream-responses-path').value = social.upstream_responses_path || '/responses';
  document.getElementById('settings-social-admin-base-url').value = social.admin_base_url || '';
  document.getElementById('settings-social-admin-verify-path').value = social.admin_verify_path || '/v1/admin/verify';
  document.getElementById('settings-social-admin-config-path').value = social.admin_config_path || '/v1/admin/config';
  document.getElementById('settings-social-admin-tokens-path').value = social.admin_tokens_path || '/v1/admin/tokens';
  document.getElementById('settings-social-model').value = social.model || 'grok-4.1-fast';
  document.getElementById('settings-social-fallback-model').value = social.fallback_model || 'grok-4.1-fast';
  document.getElementById('settings-social-cache-ttl-seconds').value = String(social.cache_ttl_seconds || 60);
  document.getElementById('settings-social-fallback-min-results').value = String(social.fallback_min_results || 3);

  document.getElementById('settings-social-admin-app-key').value = '';
  document.getElementById('settings-social-upstream-api-key').value = '';
  document.getElementById('settings-social-gateway-token').value = '';

  document.getElementById('settings-social-admin-app-key-hint').textContent =
    describeConfiguredSecret(social.admin_app_key_masked, social.admin_app_key_configured);
  document.getElementById('settings-social-upstream-api-key-hint').textContent =
    describeConfiguredSecret(social.upstream_api_key_masked, social.upstream_api_key_configured);
  document.getElementById('settings-social-gateway-token-hint').textContent =
    describeConfiguredSecret(social.gateway_token_masked, social.gateway_token_configured);

  const bits = [
    `当前模式：${socialModeLabel(social.mode || 'manual')}`,
    social.model ? `主模型：${social.model}` : '',
    social.fallback_model ? `Fallback：${social.fallback_model} (< ${social.fallback_min_results || 3})` : '',
    social.token_source ? `Token 来源：${social.token_source}` : '',
    social.admin_connected ? '后台连通正常' : '',
  ].filter(Boolean);
  if (social.error) {
    bits.push(`最近错误：${social.error}`);
  }
  document.getElementById('settings-social-meta').textContent = bits.join(' · ');
}

async function loadSettings() {
  const payload = await api('GET', '/api/settings');
  latestSettings = payload || {};
  fillSettingsForm(latestSettings);
  setStatus('settings-password-status', '');
  setStatus('settings-social-status', '');
}

async function openSettingsModal() {
  document.getElementById('settings-modal').classList.remove('hidden');
  document.body.classList.add('modal-open');
  try {
    await loadSettings();
  } catch (error) {
    setStatus('settings-social-status', `读取设置失败：${error.message}`, true);
  }
}

function closeSettingsModal() {
  document.getElementById('settings-modal').classList.add('hidden');
  document.body.classList.remove('modal-open');
}

function logoutFromSettings() {
  closeSettingsModal();
  logout();
}

function renderServiceShells() {
  const providerHtml = Object.keys(SERVICE_META).map((service) => {
    const meta = SERVICE_META[service];
    return `
      <section class="service-panel card" data-service="${service}">
        <div class="service-head">
          <div>
            <span class="service-chip" data-service="${service}">${meta.label}</span>
            <h2>${meta.label} 栏目</h2>
            <p>${meta.panelIntro} 账号前缀 ${meta.emailPrefix}，代理 Token 前缀 ${meta.tokenPrefix}。${meta.quotaSource}。</p>
          </div>
          <div class="service-tools">
            <div id="sync-meta-${service}" class="service-sync-meta">等待同步状态...</div>
            <button class="btn btn-soft" id="sync-btn-${service}" onclick="syncUsage('${service}', true)">${meta.syncButton}</button>
          </div>
        </div>
        <div class="service-body">
          <div class="stats-grid" id="overview-${service}"></div>

          <div class="section-grid">
            <div class="subcard api-shell">
              <h3>调用方式</h3>
              <p class="desc">${meta.routeHint}</p>
              <div class="inline-meta">
                <span>Base URL: <b class="mono" id="base-url-${service}"></b></span>
                <span>代理 Token 前缀: <b class="mono">${meta.tokenPrefix}</b></span>
              </div>
              <div class="code-toolbar">
                <div class="endpoint">${meta.quotaSource}</div>
                <button class="btn btn-sm" onclick="copyCode('curl-example-${service}', this)">复制示例</button>
              </div>
              <pre class="code-block mono" id="curl-example-${service}"></pre>
            </div>

            <div class="subcard">
              <h3>Token 池</h3>
              <p class="desc">${meta.tokenPoolDesc}</p>
              <div class="form-row">
                <input class="input-grow" type="text" id="token-name-${service}" placeholder="Token 备注（可选）">
                <button class="btn btn-primary" onclick="createToken('${service}')">创建 ${meta.label} Token</button>
              </div>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Token</th>
                      <th>备注</th>
                      <th>配额 / 剩余</th>
                      <th>代理统计</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody id="tokens-body-${service}"></tbody>
                </table>
          </div>
        </details>
          </div>

          <div class="subcard">
            <h3>API Key 池</h3>
            <p class="desc">${meta.keyPoolDesc}</p>
            <div class="form-row">
              <input class="input-grow mono" type="text" id="single-key-${service}" placeholder="${meta.keyPlaceholder}">
              <button class="btn btn-primary" onclick="addSingleKey('${service}')">添加 ${meta.label} Key</button>
              <button class="btn btn-soft" onclick="toggleImport('${service}')">批量导入</button>
            </div>
            <div id="import-wrap-${service}" class="toggle-area hidden">
              <textarea id="import-text-${service}" placeholder="${meta.importPlaceholder}"></textarea>
              <div class="form-row" style="margin-top:10px">
                <button class="btn btn-primary" onclick="importKeys('${service}')">导入到 ${meta.label} 池</button>
              </div>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Key</th>
                    <th>邮箱</th>
                    <th>Key 额度</th>
                    <th>账户额度</th>
                    <th>代理统计</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody id="keys-body-${service}"></tbody>
              </table>
          </div>
        </details>
        </div>
      </section>
    `;
  }).join('');

  const socialHtml = `
    <section class="service-panel card" data-service="social">
      <div class="service-head">
        <div>
          <span class="service-chip" data-service="social">Social / X</span>
          <h2>Social / X 栏目</h2>
          <p>这里收口的是 X / Social 搜索路由，不再把底层实现名字放成主标题。你看到的是 MySearch 的 Social 工作台，底层可以复用 grok2api 后台，也可以兼容别的 xAI-compatible 上游。</p>
        </div>
        <div class="service-tools">
          <div id="sync-meta-social" class="service-sync-meta">等待 Social 状态...</div>
          <div class="service-sync-meta">用于查看 token 池、剩余额度、调用次数和客户端接线方式。</div>
        </div>
      </div>
      <div class="service-body">
        <div class="stats-grid" id="overview-social"></div>
        <div class="section-grid social-section-grid">
          <div class="subcard social-board" id="social-board"></div>
          <div class="subcard api-shell" id="social-integration"></div>
        </div>
      </div>
    </section>
  `;

  document.getElementById('services-root').innerHTML = providerHtml + socialHtml;
  renderSocialBoard({});
  renderSocialIntegration({});
  renderSocialWorkspace({});
  renderServiceSwitcher({}, {});
  applyActiveService();
}

function renderServiceSwitcher(services, social) {
  const html = Object.entries(WORKSPACE_META).map(([service, meta]) => {
    const isSocial = service === 'social';
    const payload = isSocial ? {} : (services?.[service] || {});
    const quota = payload.real_quota || {};
    const socialStats = social?.stats || {};
    const remaining = isSocial ? (socialStats.chat_remaining || 0) : (quota.total_remaining ?? 0);
    const activeKeys = isSocial ? (socialStats.token_normal || 0) : (payload.keys_active || 0);
    const tokenCount = isSocial ? (socialStats.token_total || 0) : ((payload.tokens || []).length);
    const todayCount = isSocial ? (socialStats.total_calls || 0) : (payload.overview?.today_count || 0);
    const badgeList = meta.switcherBadges || [
      isSocial ? 'X Search' : `账号 ${meta.emailPrefix}`,
      isSocial ? 'compatible' : `Token ${meta.tokenPrefix}`,
      isSocial ? '自动继承' : '池子独立',
    ];
    const foot = meta.switcherFoot || (isSocial ? '统一 Social 路由 + 统一输出结构' : '独立 Key 池 + 独立额度同步');
    const metricOneLabel = isSocial ? '可用 Token' : '活跃 Key';
    const metricTwoLabel = 'Token';
    const metricThreeLabel = isSocial ? '总调用' : '今日调用';
    const metricFourLabel = isSocial ? 'Chat 剩余' : (service === 'exa' ? '实时额度' : '真实剩余');
    const metricFourValue = isSocial ? fmtNum(remaining) : (service === 'exa' ? '暂不可查' : fmtNum(remaining));

    return `
      <button
        type="button"
        class="service-toggle ${activeService === service ? 'is-active' : ''}"
        data-service="${service}"
        aria-pressed="${activeService === service ? 'true' : 'false'}"
        onclick="setActiveService('${service}')"
      >
        <div class="service-toggle-top">
          <div class="service-toggle-title">
            <span class="service-chip" data-service="${service}">${meta.label}</span>
            <strong>${meta.label}</strong>
            <span>${meta.routeHint}</span>
          </div>
          <div class="service-toggle-status">${activeService === service ? '当前查看' : '点击切换'}</div>
        </div>
        <div class="service-toggle-grid">
          <div class="service-toggle-metric">
            <div class="label">${metricOneLabel}</div>
            <div class="value">${fmtNum(activeKeys)}</div>
          </div>
          <div class="service-toggle-metric">
            <div class="label">${metricTwoLabel}</div>
            <div class="value">${fmtNum(tokenCount)}</div>
          </div>
          <div class="service-toggle-metric">
            <div class="label">${metricThreeLabel}</div>
            <div class="value">${fmtNum(todayCount)}</div>
          </div>
          <div class="service-toggle-metric">
            <div class="label">${metricFourLabel}</div>
            <div class="value">${metricFourValue}</div>
          </div>
        </div>
        <div class="service-toggle-badges">
          ${badgeList.map((badge) => `<span class="service-toggle-badge">${escapeHtml(badge)}</span>`).join('')}
        </div>
        <div class="service-toggle-foot">
          <span>${foot}</span>
          <span class="service-toggle-arrow">${activeService === service ? '正在查看' : '切换到此工作台'} →</span>
        </div>
      </button>
    `;
  }).join('');

  document.getElementById('service-switcher').innerHTML = html;
}

function applyActiveService() {
  if (!WORKSPACE_META[activeService]) {
    activeService = 'tavily';
  }

  for (const service of Object.keys(WORKSPACE_META)) {
    const panel = document.querySelector(`.service-panel[data-service="${service}"]`);
    if (!panel) continue;
    panel.classList.toggle('is-inactive', service !== activeService);
  }

  document.querySelectorAll('.service-toggle').forEach((item) => {
    const isActive = item.dataset.service === activeService;
    item.classList.toggle('is-active', isActive);
    item.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    const status = item.querySelector('.service-toggle-status');
    if (status) {
      status.textContent = isActive ? '当前查看' : '点击切换';
    }
    const arrow = item.querySelector('.service-toggle-arrow');
    if (arrow) {
      arrow.textContent = isActive ? '正在查看 →' : '切换到此工作台 →';
    }
  });

  const switcherNote = document.getElementById('switcher-note');
  if (switcherNote) {
    switcherNote.textContent = `当前工作台：${WORKSPACE_META[activeService].label} · 已记住你的切换偏好`;
  }
}

function setActiveService(service) {
  if (!WORKSPACE_META[service]) return;
  activeService = service;
  localStorage.setItem(ACTIVE_SERVICE_KEY, service);
  applyActiveService();
  const panel = document.querySelector(`.service-panel[data-service="${service}"]`);
  if (panel) {
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function doLogin(event) {
  event?.preventDefault?.();
  const input = document.getElementById('pwd-input');
  const password = input.value.trim();
  if (!password) {
    document.getElementById('login-err').textContent = '请输入管理密码。';
    document.getElementById('login-err').classList.remove('hidden');
    return;
  }
  setLoginBusy(true);
  loginWithPassword(password)
    .then(async () => {
      PWD = '';
      clearStoredPasswords();
      showDashboard();
      await refresh();
    })
    .catch((error) => {
      document.getElementById('login-err').textContent = error.message === 'Unauthorized'
        ? '密码错误。'
        : '登录失败，请检查管理 API 是否可用。';
      document.getElementById('login-err').classList.remove('hidden');
    })
    .finally(() => {
      setLoginBusy(false);
    });
}

function logout() {
  PWD = '';
  clearStoredPasswords();
  showLogin();
  closeSettingsModal();
  fetch(API + '/api/session/logout', {
    method: 'POST',
    credentials: 'same-origin',
  }).catch(() => {});
}

function fmtNum(value) {
  if (value === null || value === undefined || value === '') {
    return '--';
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toLocaleString() : String(value);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatTime(iso) {
  if (!iso) return '未同步';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '未同步';
  return date.toLocaleString();
}

function quotaBar(used, limit) {
  const safeLimit = Number(limit || 0);
  const safeUsed = Number(used || 0);
  if (!safeLimit) return '';
  const pct = Math.min(100, (safeUsed / safeLimit) * 100);
  const cls = pct >= 90 ? 'danger' : pct >= 70 ? 'warn' : '';
  return `
    <div class="quota-bar">
      <div class="quota-bar-fill ${cls}" style="width:${pct}%"></div>
    </div>
  `;
}

function buildCurlExample(service, tokenValue) {
  const baseUrl = location.origin;
  const token = tokenValue || 'YOUR_PROXY_TOKEN';
  if (service === 'firecrawl') {
    return `# Firecrawl Scrape
curl -X POST ${baseUrl}/firecrawl/v2/scrape \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${token}" \\
  -d '{"url":"https://example.com","formats":["markdown"]}'

# Firecrawl 额度查询
curl -X GET ${baseUrl}/firecrawl/v2/team/credit-usage \\
  -H "Authorization: Bearer ${token}"

# 也支持 body 里传 api_key
curl -X POST ${baseUrl}/firecrawl/v2/scrape \\
  -H "Content-Type: application/json" \\
  -d '{"api_key":"${token}","url":"https://example.com"}'`;
  }

  if (service === 'exa') {
    return `# Exa Search
curl -X POST ${baseUrl}/exa/search \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${token}" \\
  -d '{"query":"OpenAI latest model","numResults":3,"contents":{"text":true}}'

# 也支持 body 里传 api_key
curl -X POST ${baseUrl}/exa/search \\
  -H "Content-Type: application/json" \\
  -d '{"api_key":"${token}","query":"OpenAI latest model","numResults":3}'`;
  }

  return `# Tavily Search
curl -X POST ${baseUrl}/api/search \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${token}" \\
  -d '{"query":"hello world","max_results":1}'

# Tavily Extract
curl -X POST ${baseUrl}/api/extract \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${token}" \\
  -d '{"urls":["https://example.com"]}'

# 也支持 body 里传 api_key
curl -X POST ${baseUrl}/api/search \\
  -H "Content-Type: application/json" \\
  -d '{"api_key":"${token}","query":"hello world"}'`;
}

function renderGlobalSummary(services, social) {
  const list = Object.values(services || {});
  const totalKeys = list.reduce((sum, item) => sum + Number(item.keys_total || 0), 0);
  const totalTokens = list.reduce((sum, item) => sum + Number((item.tokens || []).length), 0);
  const todayCount = list.reduce((sum, item) => sum + Number(item.overview?.today_count || 0), 0);
  const syncable = list.filter((item) => SERVICE_META[item.service]?.syncSupported !== false);
  const realRemaining = syncable.reduce((sum, item) => sum + Number(item.real_quota?.total_remaining || 0), 0);
  const syncedKeys = syncable.reduce((sum, item) => sum + Number(item.real_quota?.synced_keys || 0), 0);
  const syncableLabels = syncable.map((item) => item.label).filter(Boolean).join(' / ') || '暂无';
  const socialStats = social?.stats || {};
  const socialMode = socialModeLabel(social?.mode || 'manual');

  document.getElementById('global-summary').innerHTML = `
    <div class="summary-box">
      <div class="label">代理 Token</div>
      <div class="value">${fmtNum(totalTokens)}</div>
      <div class="hint">${fmtNum(totalKeys)} 个 Key 已导入，按服务独立签发</div>
    </div>
    <div class="summary-box">
      <div class="label">今日调用</div>
      <div class="value">${fmtNum(todayCount)}</div>
      <div class="hint">来自本地 usage_logs 聚合</div>
    </div>
    <div class="summary-box">
      <div class="label">官方剩余</div>
      <div class="value">${fmtNum(realRemaining)}</div>
      <div class="hint">已同步 ${fmtNum(syncedKeys)} 个 Key · ${escapeHtml(syncableLabels)}</div>
    </div>
    <div class="summary-box">
      <div class="label">Social / X</div>
      <div class="value">${fmtNum(socialStats.token_total || 0)}</div>
      <div class="hint">${social?.admin_connected ? `模式 ${socialMode}` : '等待后台接通'}</div>
    </div>
    <div class="summary-box">
      <div class="label">Social Chat</div>
      <div class="value">${fmtNum(socialStats.chat_remaining || 0)}</div>
      <div class="hint">Image ${fmtNum(socialStats.image_remaining || 0)} · 调用 ${fmtNum(socialStats.total_calls || 0)}</div>
    </div>
    <div class="summary-box">
      <div class="label">MySearch Token</div>
      <div class="value">${fmtNum(latestMySearch?.token_count || 0)}</div>
      <div class="hint">给 MCP / Skill / 客户端统一接入</div>
    </div>
  `;
}

function renderSyncMeta(service, payload) {
  if (service === 'exa') {
    const detail = payload.usage_sync?.detail || 'Exa 实时额度暂时无法查询';
    document.getElementById(`sync-meta-${service}`).textContent = [
      `已导入 ${fmtNum(payload.keys_total || 0)} 个 Key`,
      `已签发 ${fmtNum((payload.tokens || []).length)} 个 Token`,
      detail,
    ].join(' · ');
    return;
  }

  const quota = payload.real_quota || {};
  const usageSync = payload.usage_sync || {};
  const parts = [];

  parts.push(`已同步 ${fmtNum(quota.synced_keys || 0)} / ${fmtNum(quota.total_keys || 0)} 个 Key`);
  if ((quota.key_level_count || 0) > 0) {
    parts.push(`Key 级额度 ${fmtNum(quota.key_level_count)}`);
  }
  if ((quota.account_fallback_count || 0) > 0) {
    parts.push(`账户正常数量：${fmtNum(quota.account_fallback_count)}`);
  }
  if (quota.last_synced_at) {
    parts.push(`最近同步 ${formatTime(quota.last_synced_at)}`);
  }
  if ((quota.error_keys || 0) > 0) {
    parts.push(`错误 ${fmtNum(quota.error_keys)}`);
  }
  if ((usageSync.synced || 0) > 0 || (usageSync.errors || 0) > 0) {
    parts.push(`本轮同步 ${fmtNum(usageSync.synced || 0)} 成功 / ${fmtNum(usageSync.errors || 0)} 失败`);
  }

  document.getElementById(`sync-meta-${service}`).textContent = parts.join(' · ');
}

function renderOverview(service, payload) {
  const overview = payload.overview || {};
  const quota = payload.real_quota || {};

  if (service === 'exa') {
    const todayCount = Number(overview.today_count || 0);
    const todaySuccess = Number(overview.today_success || 0);
    const successRate = todayCount ? `${Math.round((todaySuccess / todayCount) * 100)}%` : '暂无';

    document.getElementById(`overview-${service}`).innerHTML = `
      <div class="stat-box">
        <div class="label">实时额度</div>
        <div class="value">暂时无法查询</div>
        <div class="hint">控制台当前只统计 Exa 代理调用</div>
      </div>
      <div class="stat-box">
        <div class="label">Key 池状态</div>
        <div class="value">${fmtNum(payload.keys_active || 0)} <span class="muted">/ ${fmtNum(payload.keys_total || 0)}</span></div>
        <div class="hint">活跃 / 总数</div>
      </div>
      <div class="stat-box">
        <div class="label">Token 池状态</div>
        <div class="value">${fmtNum((payload.tokens || []).length)}</div>
        <div class="hint">Exa 独立代理 Token 池</div>
      </div>
      <div class="stat-box">
        <div class="label">今日代理调用</div>
        <div class="value">${fmtNum(overview.today_count || 0)}</div>
        <div class="hint">成功 ${fmtNum(overview.today_success || 0)} / 失败 ${fmtNum(overview.today_failed || 0)}</div>
      </div>
      <div class="stat-box">
        <div class="label">本月代理调用</div>
        <div class="value">${fmtNum(overview.month_count || 0)}</div>
        <div class="hint">本月成功 ${fmtNum(overview.month_success || 0)}</div>
      </div>
      <div class="stat-box">
        <div class="label">今日成功率</div>
        <div class="value">${successRate}</div>
        <div class="hint">${payload.usage_sync?.detail || 'Exa 实时额度暂时无法查询，后续如果接入官方读取会补充显示。'}</div>
      </div>
    `;
    return;
  }

  const totalLimit = Number(quota.total_limit || 0);
  const totalUsed = Number(quota.total_used || 0);
  const totalRemaining = Number(quota.total_remaining || 0);
  const remainStyle = totalLimit && totalUsed / totalLimit >= 0.9
    ? 'color: var(--danger)'
    : totalLimit && totalUsed / totalLimit >= 0.7
      ? 'color: var(--warn)'
      : 'color: var(--ok)';

  document.getElementById(`overview-${service}`).innerHTML = `
    <div class="stat-box">
      <div class="label">真实总额度</div>
      <div class="value">${fmtNum(totalLimit)}</div>
      <div class="hint">${SERVICE_META[service].quotaSource}</div>
    </div>
    <div class="stat-box">
      <div class="label">真实已用</div>
      <div class="value">${fmtNum(totalUsed)}</div>
      <div class="hint">按已同步 Key 汇总</div>
    </div>
    <div class="stat-box">
      <div class="label">真实剩余</div>
      <div class="value" style="${remainStyle}">${fmtNum(totalRemaining)}</div>
      <div class="hint">${quotaBar(totalUsed, totalLimit) || '尚未获得完整上限信息'}</div>
    </div>
    <div class="stat-box">
      <div class="label">Key 池状态</div>
      <div class="value">${fmtNum(payload.keys_active || 0)} <span class="muted">/ ${fmtNum(payload.keys_total || 0)}</span></div>
      <div class="hint">活跃 / 总数</div>
    </div>
    <div class="stat-box">
      <div class="label">今日代理调用</div>
      <div class="value">${fmtNum(overview.today_count || 0)}</div>
      <div class="hint">成功 ${fmtNum(overview.today_success || 0)} / 失败 ${fmtNum(overview.today_failed || 0)}</div>
    </div>
    <div class="stat-box">
      <div class="label">本月代理调用</div>
      <div class="value">${fmtNum(overview.month_count || 0)}</div>
      <div class="hint">成功 ${fmtNum(overview.month_success || 0)}</div>
    </div>
  `;
}

function renderSocialWorkspace(social) {
  const stats = social?.stats || {};
  const mode = socialModeLabel(social?.mode || 'manual');
  const source = socialTokenSourceLabel(social?.token_source || '');
  const syncLine = [
    mode,
    `Token ${fmtNum(stats.token_total || 0)}`,
    `Chat ${fmtNum(stats.chat_remaining || 0)}`,
    `总调用 ${fmtNum(stats.total_calls || 0)}`,
  ].join(' · ');

  const syncMeta = document.getElementById('sync-meta-social');
  if (syncMeta) {
    syncMeta.textContent = social?.error ? `${syncLine} · 最近错误 ${social.error}` : syncLine;
  }

  document.getElementById('overview-social').innerHTML = `
    <div class="stat-box">
      <div class="label">工作模式</div>
      <div class="value">${escapeHtml(mode)}</div>
      <div class="hint">当前 Social / X 工作台的路由状态</div>
    </div>
    <div class="stat-box">
      <div class="label">Token 正常 / 总数</div>
      <div class="value">${fmtNum(stats.token_normal || 0)} <span class="muted">/ ${fmtNum(stats.token_total || 0)}</span></div>
      <div class="hint">兼容上游 token 池实时汇总</div>
    </div>
    <div class="stat-box">
      <div class="label">Chat 剩余</div>
      <div class="value">${fmtNum(stats.chat_remaining || 0)}</div>
      <div class="hint">Image ${fmtNum(stats.image_remaining || 0)} · Video ${stats.video_remaining === null ? '无法统计' : fmtNum(stats.video_remaining)}</div>
    </div>
    <div class="stat-box">
      <div class="label">Token 来源</div>
      <div class="value">${escapeHtml(source)}</div>
      <div class="hint">${social?.admin_connected ? '当前已接入后台自动继承' : '当前为手动或混合模式'}</div>
    </div>
  `;
}

function renderApiExample(service, tokens) {
  const firstToken = tokens && tokens.length ? tokens[0].token : 'YOUR_PROXY_TOKEN';
  document.getElementById(`base-url-${service}`).textContent = location.origin;
  document.getElementById(`curl-example-${service}`).textContent = buildCurlExample(service, firstToken);
}

function renderTokenQuota(token) {
  return '<div class="table-note"><b style="color: var(--ok)">无限制</b><br><span class="muted">已关闭小时 / 日 / 月限流</span></div>';
}

function renderTokens(service, tokens) {
  const tbody = document.getElementById(`tokens-body-${service}`);
  if (!tokens || tokens.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="muted">当前还没有 Token，先创建一个给下游使用。</td></tr>';
    return;
  }

  tbody.innerHTML = tokens.map((token) => {
    const stats = token.stats || {};
    return `
      <tr>
        <td class="mono">${maskToken(token.token)}</td>
        <td>${escapeHtml(token.name || '-')}</td>
        <td>${renderTokenQuota(token)}</td>
        <td class="table-note">
          今日成功 ${fmtNum(stats.today_success || 0)} / 失败 ${fmtNum(stats.today_failed || 0)}<br>
          本月成功 ${fmtNum(stats.month_success || 0)}<br>
          小时调用 ${fmtNum(stats.hour_count || 0)}
        </td>
        <td>
          <div class="table-actions">
            <button class="btn btn-sm" onclick='copyText(${JSON.stringify(token.token)}, this)'>复制</button>
            <button class="btn btn-sm btn-danger" onclick="delToken(${token.id})">删除</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function renderKeyQuota(service, key) {
  if (service === 'exa') {
    return `
      <div class="table-note muted">Exa 实时额度暂时无法查询。</div>
      <div class="table-note muted">当前只展示代理层调用统计。</div>
      ${key.usage_sync_error ? `<div class="table-note" style="color: var(--warn); margin-top: 6px">最近错误: ${escapeHtml(key.usage_sync_error)}</div>` : ''}
    `;
  }

  if (key.usage_key_limit !== null && key.usage_key_used !== null) {
    const remain = key.usage_key_remaining ?? Math.max(0, key.usage_key_limit - key.usage_key_used);
    return `
      <div class="table-note">已用 ${fmtNum(key.usage_key_used)} / ${fmtNum(key.usage_key_limit)}</div>
      <div class="table-note"><b style="color:${remain > 0 ? 'var(--ok)' : 'var(--danger)'}">剩余 ${fmtNum(remain)}</b></div>
      ${quotaBar(key.usage_key_used, key.usage_key_limit)}
      <div class="table-note muted" style="margin-top:6px">同步 ${formatTime(key.usage_synced_at)}</div>
      ${key.usage_sync_error ? `<div class="table-note" style="color: var(--warn); margin-top: 6px">最近错误: ${escapeHtml(key.usage_sync_error)}</div>` : ''}
    `;
  }

  if (service === 'firecrawl' && key.usage_synced_at) {
    return `
      <div class="table-note muted">Firecrawl 当前主要返回账户级 credits。</div>
      <div class="table-note muted">单 Key 独立限额请看右侧账户额度。</div>
      ${key.usage_sync_error ? `<div class="table-note" style="color: var(--warn); margin-top: 6px">最近错误: ${escapeHtml(key.usage_sync_error)}</div>` : ''}
    `;
  }

  if (key.usage_sync_error) {
    return `<div class="table-note" style="color: var(--warn)">同步失败：${escapeHtml(key.usage_sync_error)}</div>`;
  }

  return '<span class="muted">未同步</span>';
}

function renderAccountQuota(service, key) {
  if (service === 'exa') {
    return '<span class="muted">Exa 账户实时额度暂时无法查询</span>';
  }

  if (key.usage_account_limit !== null && key.usage_account_used !== null) {
    const remain = key.usage_account_remaining ?? Math.max(0, key.usage_account_limit - key.usage_account_used);
    const plan = key.usage_account_plan || (service === 'firecrawl' ? 'Firecrawl Credits' : '未知计划');
    return `
      <div class="table-note">${escapeHtml(plan)}</div>
      <div class="table-note">已用 ${fmtNum(key.usage_account_used)} / ${fmtNum(key.usage_account_limit)}</div>
      <div class="table-note"><b style="color:${remain > 0 ? 'var(--ok)' : 'var(--danger)'}">剩余 ${fmtNum(remain)}</b></div>
      ${quotaBar(key.usage_account_used, key.usage_account_limit)}
    `;
  }
  return '<span class="muted">未返回</span>';
}

function renderKeys(service, keys) {
  const tbody = document.getElementById(`keys-body-${service}`);
  if (!keys || keys.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="muted">当前服务还没有导入 Key。</td></tr>';
    return;
  }

  tbody.innerHTML = keys.map((key) => {
    const active = Number(key.active) === 1;
    return `
      <tr>
        <td>${fmtNum(key.id)}</td>
        <td class="mono">${escapeHtml(key.key_masked || key.key)}</td>
        <td>${escapeHtml(key.email || '-')}</td>
        <td>${renderKeyQuota(service, key)}</td>
        <td>${renderAccountQuota(service, key)}</td>
        <td class="table-note">
          成功 ${fmtNum(key.total_used || 0)}<br>
          失败 ${fmtNum(key.total_failed || 0)}<br>
          最近使用 ${formatTime(key.last_used_at)}
        </td>
        <td><span class="tag ${active ? 'tag-ok' : 'tag-off'}">${active ? '正常' : '禁用'}</span></td>
        <td>
          <div class="table-actions">
            <button class="btn btn-sm" onclick="toggleKey(${key.id}, ${active ? 0 : 1})">${active ? '禁用' : '启用'}</button>
            <button class="btn btn-sm btn-danger" onclick="delKey(${key.id})">删除</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

async function refresh(options = {}) {
  const force = options.force ? '?force=1' : '';
  const payload = await api('GET', `/api/stats${force}`);
  const services = payload.services || {};
  const social = payload.social || {};
  const mysearch = payload.mysearch || {};
  latestServices = services;
  latestSocial = social;
  latestMySearch = mysearch;

  renderGlobalSummary(services, social);
  renderMySearchQuickstart(mysearch, social);
  renderSocialBoard(social);
  renderSocialIntegration(social);
  renderSocialWorkspace(social);
  renderServiceSwitcher(services, social);
  for (const [service, meta] of Object.entries(SERVICE_META)) {
    const servicePayload = services[service] || {
      tokens: [],
      keys: [],
      overview: {},
      real_quota: {},
      usage_sync: {},
      keys_total: 0,
      keys_active: 0,
    };
    renderSyncMeta(service, servicePayload);
    renderOverview(service, servicePayload);
    renderApiExample(service, servicePayload.tokens || []);
    renderTokens(service, servicePayload.tokens || []);
    renderKeys(service, servicePayload.keys || []);
    const syncButton = document.getElementById(`sync-btn-${service}`);
    const syncSupported = servicePayload.usage_sync?.supported !== false && meta.syncSupported !== false;
    syncButton.textContent = syncSupported ? meta.syncButton : '暂不支持同步';
    syncButton.disabled = !syncSupported;
    syncButton.title = syncSupported ? '' : (servicePayload.usage_sync?.detail || meta.quotaSource);
  }
  applyActiveService();
}

function toggleImport(service) {
  document.getElementById(`import-wrap-${service}`).classList.toggle('hidden');
}

async function createToken(service) {
  const input = document.getElementById(`token-name-${service}`);
  await api('POST', '/api/tokens', {
    service,
    name: input.value.trim(),
  });
  input.value = '';
  await refresh({ force: true });
}

async function delToken(id) {
  if (!confirm('确认删除这个 Token 吗？')) return;
  await api('DELETE', `/api/tokens/${id}`);
  await refresh({ force: true });
}

async function addSingleKey(service) {
  const input = document.getElementById(`single-key-${service}`);
  const key = input.value.trim();
  if (!key) return;
  await api('POST', '/api/keys', { service, key });
  input.value = '';
  await refresh({ force: true });
}

async function importKeys(service) {
  const textarea = document.getElementById(`import-text-${service}`);
  const text = textarea.value.trim();
  if (!text) return;
  const result = await api('POST', '/api/keys', { service, file: text });
  textarea.value = '';
  document.getElementById(`import-wrap-${service}`).classList.add('hidden');
  showToast(`已导入 ${result.imported || 0} 个 ${SERVICE_META[service].label} Key`, 'success');
  await refresh({ force: true });
}

async function delKey(id) {
  if (!confirm('确认删除这个 Key 吗？')) return;
  await api('DELETE', `/api/keys/${id}`);
  await refresh({ force: true });
}

async function toggleKey(id, active) {
  await api('PUT', `/api/keys/${id}/toggle`, { active });
  await refresh({ force: true });
}

async function syncUsage(service, force) {
  if (SERVICE_META[service]?.syncSupported === false) {
    showToast(SERVICE_META[service].quotaSource, 'warn');
    return;
  }
  const button = document.getElementById(`sync-btn-${service}`);
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = '同步中...';
  try {
    await api('POST', '/api/usage/sync', { service, force });
    await refresh({ force: true });
  } catch (error) {
    showToast(`同步 ${SERVICE_META[service].label} 额度失败: ${error.message}`, 'error');
    button.disabled = false;
    button.textContent = originalText;
  }
}

async function changePwd(event) {
  event?.preventDefault?.();
  const input = document.getElementById('settings-new-pwd');
  const password = input.value.trim();
  if (password.length < 4) {
    setStatus('settings-password-status', '密码至少 4 位。', true);
    return;
  }
  try {
    await api('PUT', '/api/password', { password });
    PWD = password;
    localStorage.setItem(STORAGE_KEY, password);
    localStorage.removeItem(LEGACY_STORAGE_KEY);
    input.value = '';
    setStatus('settings-password-status', '密码已更新，当前会话也已同步。');
  } catch (error) {
    setStatus('settings-password-status', `保存密码失败：${error.message}`, true);
  }
}

async function saveSocialSettings(event) {
  event?.preventDefault?.();
  const body = {
    upstream_base_url: document.getElementById('settings-social-upstream-base-url').value.trim(),
    upstream_responses_path: document.getElementById('settings-social-upstream-responses-path').value.trim(),
    admin_base_url: document.getElementById('settings-social-admin-base-url').value.trim(),
    admin_verify_path: document.getElementById('settings-social-admin-verify-path').value.trim(),
    admin_config_path: document.getElementById('settings-social-admin-config-path').value.trim(),
    admin_tokens_path: document.getElementById('settings-social-admin-tokens-path').value.trim(),
    model: document.getElementById('settings-social-model').value.trim(),
    fallback_model: document.getElementById('settings-social-fallback-model').value.trim(),
    cache_ttl_seconds: document.getElementById('settings-social-cache-ttl-seconds').value.trim(),
    fallback_min_results: document.getElementById('settings-social-fallback-min-results').value.trim(),
  };

  const adminAppKey = document.getElementById('settings-social-admin-app-key').value.trim();
  const upstreamApiKey = document.getElementById('settings-social-upstream-api-key').value.trim();
  const gatewayToken = document.getElementById('settings-social-gateway-token').value.trim();

  if (adminAppKey) body.admin_app_key = adminAppKey;
  if (upstreamApiKey) body.upstream_api_key = upstreamApiKey;
  if (gatewayToken) body.gateway_token = gatewayToken;

  try {
    const payload = await api('PUT', '/api/settings/social', body);
    latestSettings = payload || {};
    fillSettingsForm(latestSettings);
    setStatus('settings-social-status', 'Social / X 设置已保存，当前控制台状态已刷新。');
    await refresh({ force: true });
  } catch (error) {
    setStatus('settings-social-status', `保存 Social / X 设置失败：${error.message}`, true);
  }
}

function flashButtonLabel(button, label) {
  if (!button) return;
  const original = button.textContent;
  button.textContent = label;
  setTimeout(() => {
    button.textContent = original;
  }, 1400);
}

async function writeClipboardText(text) {
  const value = String(text ?? '');

  if (navigator.clipboard?.writeText && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch (error) {
      console.warn('Clipboard API failed, falling back to execCommand copy.', error);
    }
  }

  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.top = '0';
  textarea.style.left = '-9999px';
  textarea.style.opacity = '0';
  textarea.style.pointerEvents = 'none';

  document.body.appendChild(textarea);

  const selection = document.getSelection();
  const ranges = [];
  if (selection) {
    for (let index = 0; index < selection.rangeCount; index += 1) {
      ranges.push(selection.getRangeAt(index).cloneRange());
    }
  }

  textarea.focus({ preventScroll: true });
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  let copied = false;
  try {
    copied = document.execCommand('copy');
  } finally {
    textarea.remove();
    if (selection) {
      selection.removeAllRanges();
      ranges.forEach((range) => selection.addRange(range));
    }
  }

  if (!copied) {
    throw new Error('Clipboard copy command was rejected');
  }

  return true;
}

async function copyCode(elementId, button) {
  const source = document.getElementById(elementId);
  if (!source) {
    flashButtonLabel(button, '未找到');
    return;
  }

  try {
    await writeClipboardText(source.textContent);
    flashButtonLabel(button, '已复制');
  } catch (error) {
    console.error(`Copy failed for #${elementId}`, error);
    flashButtonLabel(button, '复制失败');
  }
}

async function copyText(value, button) {
  try {
    await writeClipboardText(value);
    flashButtonLabel(button, '已复制');
  } catch (error) {
    console.error('Copy failed for inline value', error);
    flashButtonLabel(button, '复制失败');
  }
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !document.getElementById('settings-modal').classList.contains('hidden')) {
    closeSettingsModal();
  }
});

renderServiceShells();

async function initConsole() {
  if (INITIAL_AUTHENTICATED) {
    showDashboard();
    try {
      await refresh();
    } catch (error) {
      if (error.message === 'Unauthorized') {
        showLogin();
        return;
      }
      document.getElementById('login-err').textContent = `控制台加载失败：${error.message}`;
      document.getElementById('login-err').classList.remove('hidden');
    }
    return;
  }

  const migrated = await migrateStoredPasswordIfNeeded();
  const hasSession = migrated || await hasServerSession();
  if (!hasSession) {
    showLogin();
    return;
  }
  showDashboard();
  try {
    await refresh();
  } catch (error) {
    if (error.message === 'Unauthorized') {
      showLogin();
      return;
    }
    document.getElementById('login-err').textContent = `控制台加载失败：${error.message}`;
    document.getElementById('login-err').classList.remove('hidden');
  }
}

initConsole();

function setActiveSettingsTab(tabName) {
  document.querySelectorAll('.settings-tab').forEach(btn => {
    btn.classList.toggle('is-active', btn.dataset.settingsTab === tabName);
  });
  document.querySelectorAll('.settings-tab-panel').forEach(panel => {
    panel.classList.toggle('hidden', panel.dataset.settingsPanel !== tabName);
    panel.classList.toggle('is-active', panel.dataset.settingsPanel === tabName);
  });
}


function maskToken(token) {
  if (!token) return '';
  if (token.length <= 12) return '****';
  return token.slice(0, 5) + '****' + token.slice(-4);
}


async function saveTavilySettings(event) {
  event?.preventDefault?.();
  const body = {
    mode: document.getElementById('settings-tavily-mode').value,
    upstream_base_url: document.getElementById('settings-tavily-upstream-base-url').value.trim(),
    upstream_search_path: document.getElementById('settings-tavily-upstream-search-path').value.trim(),
    upstream_extract_path: document.getElementById('settings-tavily-upstream-extract-path').value.trim(),
  };
  const upstreamApiKey = document.getElementById('settings-tavily-upstream-api-key').value.trim();
  if (upstreamApiKey) body.upstream_api_key = upstreamApiKey;

  try {
    const payload = await api('PUT', '/api/settings/tavily', body);
    latestSettings = payload || {};
    fillSettingsForm(latestSettings);
    setStatus('settings-tavily-status', 'Tavily 设置已保存。');
    await refresh({ force: true });
  } catch (error) {
    setStatus('settings-tavily-status', `保存失败：${error.message}`, true);
  }
}
