# 前端（Vue 3 + Vite 5 SPA，AIOps 生产运维控制台，含构建/部署链路） 体检

## 现状设计

前端是 Vue 3 + Vite 5 的纯 SPA（`type: module`，全 JS 无 TypeScript），13 条路由全部 `() => import()` 懒加载，用 `createWebHashHistory` 做 hash 路由，UI 层是 ant-design-vue 4 通过 `app.use()` 手动注册 28 个组件（main.js:40-69），全局状态只有两个 Pinia store：auth（token + user）与 theme（写 CSS 变量 + antd token）。认证走 Bearer JWT：axios 请求拦截器每次从 localStorage 读 token 注入 `Authorization` 头（api/index.js:11-15），401 时清 localStorage 并 `location.assign('#/login')`，而路由守卫直接读 localStorage 而不读 store（router/index.js:39），存在两份登录态。诊断流式输出是自研的 fetch-based SSE 客户端（answerStream.js），带 `Last-Event-ID` 续传、45s idle 看门狗、最多 3 次重试，失败后自动降级为 2.5s 轮询（useDiagnosisChat.js:119-156）；诊断 run 由后端持久化，页面重进通过 `watchExistingRun` 接回（:282-283）。Markdown 统一走 `DOMPurify.sanitize(marked.parse(text))`（:426），其余交互数据靠 30s `setInterval` 轮询（App.vue:102、useMetricsPolling.js:22-25）加 window 自定义事件 `aiops:messages-changed` / `aiops:approvals-changed` 做跨组件刷新（App.vue:99-100）。生产由 Caddy（宿主机，指向 dist-prod）或 Docker 多阶段构建 + nginx 同域反代 API，`VITE_API_BASE` 留空走相对路径。

## 做得好的地方

- XSS 面收得很干净：唯二带动态内容的 v-html 都走 `DOMPurify.sanitize(markedParser.parse(text))`（useDiagnosisChat.js:425-428 → DiagnosisPanel.vue:287,301、AnswerDraft.vue:11,13），而 App.vue:161 的 `v-html="iconPath(item.icon)"` 只渲染 App.vue:49-62 里的静态内联 SVG 常量表，完全不接触用户/模型输入；marked 未加载完成时还有 `escapeHtml()` 先转义再 `<br />` 的兜底（:24-31,427），顺序也没有写反（先 parse 后 sanitize）。
- 自研 SSE 传输实现得相当扎实：用 fetch + `Authorization` 头而不是把 token 放进 URL（answerStream.js:2,28），支持 `Last-Event-ID` 断点续传（:29,53,58），45s idle 看门狗主动 abort 死连接（:23-26），有限次重试 + 线性退避（:18,79），单帧 4MB 上限防内存爆（:44），AbortController 一路贯穿到 `onUnmounted`（useDiagnosisChat.js:181-183）；`attempt = 0`（:59）让健康的长连接不因重连计数耗尽而中断，是很少见的正确细节。
- 流式失败有完整的降级与接管路径：SSE 不可用时自动切到 2.5s 轮询（useDiagnosisChat.js:152-153），并有 27 分钟总超时兜底（:107,144），失败提示明确引导用户「从历史查看」；诊断报告在服务端持久化，重进页面由 `watchExistingRun`（:282-283）把仍在 running 的 run 接回并继续渲染 liveSteps，做到了「关页面不丢任务」，这对生产故障处置是关键能力。
- loading 与错误复位纪律好：每个列表页都有 loading ref 绑到 `a-spin`/按钮，且用 `finally` 保证复位（ApprovalsPage.vue:114-116、MessagesPage.vue:415-419、MonitoringCenterPage.vue:87-89）；执行类按钮用 `:loading="actionKey === ..."` 做单按钮防重（ApprovalsPage.vue:232,244、RemoteActionsCard.vue:246），经核对 antd Button 在 loading 时会 `event.preventDefault()` 拦截点击（node_modules/ant-design-vue/es/button/button.js:122-128），因此同一按钮双击不会重复提交。
- 构建与静态资源策略是有意识的工程取舍：路由级懒加载 12 个页面（router/index.js:3-14），`marked` 走动态 import（useDiagnosisChat.js:35）且产物验证确实没进首屏预载（dist-prod/index.html 只有 6 条 modulepreload，不含 vendor-markdown），manualChunks 做了 vendor 分组（vite.config.js:27-51），Caddy 对 hash 资源设 `immutable` 长缓存、对 index.html 设 `no-store`（Caddyfile:27-44）。构建产物卫生也好：`dist/`、`dist-prod/` 均被 gitignore（frontend/.gitignore:2-3），`git ls-files | grep -c dist` = 0，没有把 1.6MB 产物提交进仓库。
- 凭证录入做成了 write-only：服务端掩码 + `a-input-password` + 点「修改」才进入编辑态（NotifyConfigCard.vue:254-262,339-346、useNotifyConfig.js:114-119 的 `***` 回传逻辑），新建 Token 的明文只显示一次并明确提示「仅显示一次 / 请立即保存明文」（SystemTokensCard.vue:145-148,88），符合业界 secret 处理的最低要求。
- 主题状态集中管理得当：stores/theme.js:28-76 用一张 CSS_VARS 映射同时驱动自定义 CSS 变量与 antd `token`/`algorithm`，避免每个组件各自管理颜色；App.vue:120 与 LoginPage.vue:46 各自包一层 `a-config-provider` 保证登录页也吃到主题。

## 发现

### [high] 审批中心「批准执行」无二次确认，一次误点直接对生产系统下发处置动作

- **证据**：frontend/src/pages/ApprovalsPage.vue:242-250 的批准按钮直接 `@click="decide(item, true)"`，`decide()`（:131-148）紧接着就 `api.post(`/systems/${item.system_id}/workflow/${item.id}/decision`, { approved })` 并 `message.success('已批准并执行')`，全程没有任何确认步骤。对照同一代码库：受控重启用的是 `Modal.confirm`（frontend/src/features/system-detail/pages/SystemDetailPage.vue:90-100，含 danger okButtonProps），删除类动作用 `a-popconfirm`（ServicesConfigCard.vue:145、SystemTokensCard.vue:187、SystemsPage.vue:154），唯独风险最高的「批准并对生产执行 restart_container / run_redis_command」是裸点击。另外 ApprovalsPage.vue:160 的 `actionKey` 是全局单值，只能对当前卡片做 loading，其它卡片的按钮在请求飞行期间依然可点。
- **风险**：审批卡片在 `approval-feed` 里纵向连排、按钮固定在每张卡片右下角（ApprovalsPage.vue:232-250），在滚动/触控板上误点或连点的概率很高，而一次点击就等于一次真实生产变更（重启容器、执行 Redis 命令），没有 dry-run、没有后悔窗口，事后只能靠审计日志回查。影响面是全部已接入系统；在事故处置期间（多人同时操作审批中心）触发概率显著上升。
- **业界做法**：变更类操作普遍要求二段确认：Ant Design/Material 的 confirm dialog，Terraform Cloud 的 apply 需要人工 confirm，Argo CD 的 manual sync 配 sync window + 二次确认，AWS Console 删除资源要求勾选/输入资源名。ITIL/ServiceNow、Jira Service Management 的变更审批强制填写审批意见并留痕，高风险变更还要求 CAB 双人复核（four-eyes principle）。
- **可采纳性**：easy
- **建议**：在 ApprovalsPage.vue 的 approve 分支改为 `Modal.confirm`，正文展示 `actionSummary(item)`、`actionRisk(item)` 与 `actionVerification(item)`（这些数据卡片上已有，:216-224），`okButtonProps: { danger: actionRisk(item) === 'high' }`；对 high 风险要求输入 `item.system_name` 才可提交。同时给 `decide()` 加 `if (actionKey.value) return` 防重入，并把 `actionKey` 从单值改为 `Set`，让飞行中的其它卡片一并禁用。收益：消除误点即变更这一类最不可逆的操作风险。

### [high] JWT 存 localStorage，且整条部署链路没有任何 CSP 与安全响应头

- **证据**：令牌写入与读取全在 localStorage：frontend/src/stores/auth.js:6 `token: localStorage.getItem('token') || ''`、:15 `localStorage.setItem('token', token)`、:42 `removeItem`；frontend/src/api/index.js:12 与 frontend/src/router/index.js:39 也各自直接读 localStorage（守卫不读 store）。同时 frontend/index.html:1-13 无 CSP meta，deploy/frontend.nginx.conf:1-19 未设置任何安全头（无 CSP、X-Content-Type-Options、X-Frame-Options、Referrer-Policy、HSTS），Caddyfile:1-45 也只有 Cache-Control；对全仓 `grep -rn "Content-Security-Policy\|X-Content-Type-Options\|Strict-Transport"` 仅命中第三方 venv，生产链路确实一处都没有。
- **风险**：localStorage 中的令牌对任何同源 JS 完全可读，XSS 一旦发生即为「令牌立即外带」，且是长期有效的 Bearer，攻击者可离线长期越权操作生产系统（这正是本平台最敏感的权限）。当前 DOMPurify 用得正确，唯一的动态 v-html 已被净化，所以现状是「可被利用但暂无已知入口」，定级 high 而非 critical；但只要将来新增一处 v-html、或某个 npm 依赖被投毒、或 DOMPurify 出现绕过，就立刻升级为可复现的生产接管。缺 CSP 意味着没有任何兜底（无法阻止内联脚本执行与外连 exfiltration）。
- **业界做法**：主流做法是 httpOnly + Secure + SameSite 的会话 Cookie（SPA 场景推荐 BFF 模式，Auth0/Okta 官方对 SPA 的建议即为 Backend-for-Frontend），配合 CSRF 双提交 token；同时强制下发 CSP（`default-src 'self'; script-src 'self' 'nonce-...'`，`connect-src` 限定到 API 域）、`X-Content-Type-Options: nosniff`、HSTS。Grafana、Datadog、云厂商控制台普遍使用短时 access token + refresh 轮换，并把 CSP 作为控制台的强制基线。
- **可采纳性**：moderate
- **建议**：两步走。短期（当周可交付）：在 deploy/frontend.nginx.conf 与 Caddyfile 的 server 块统一加 CSP（含 `connect-src 'self'`、`object-src 'none'`、`base-uri 'self'`）、`X-Content-Type-Options: nosniff`、`Referrer-Policy: same-origin`、`X-Frame-Options: DENY`，用 report-only 起跑一轮再切强制。中期：由后端 `/auth/login` 下发 httpOnly Cookie，前端改动面很小——api/index.js:11-15 去掉 Authorization 拦截器、answerStream.js:28 的 `Authorization` 换成 `credentials: 'include'`（:31 已经写了 include）、api/index.js:22 的清理逻辑改为调用 `POST /auth/logout`；收益是把「XSS 即失窃」降级为「XSS 仅当前会话内可用」。

### [high] 本次 SSE 改造的核心文件未入库，仓库 HEAD 仍是旧的轮询实现

- **证据**：`git status --short frontend/` 给出：`?? frontend/src/features/diagnostics/answerStream.js`、`?? frontend/src/features/diagnostics/components/AnswerDraft.vue`、`?? frontend/src/features/diagnostics/components/EvidenceValidation.vue`、` M frontend/src/features/diagnostics/useDiagnosisChat.js`、` D frontend/src/features/diagnostics/parseAnswer.js`。对照 HEAD：`git show HEAD:frontend/src/features/diagnostics/components/DiagnosisPanel.vue` 里是 `import { parseAnswer } from '../parseAnswer'`，而工作区的 DiagnosisPanel.vue:2-3 已改为 `import AnswerDraft from './AnswerDraft.vue'`；`git show HEAD:.../useDiagnosisChat.js` 首行只有 `import { nextTick, onMounted, ref } from 'vue'`（无 `onUnmounted`、无 `subscribeDiagnosis`）。也就是说 answerStream.js / AnswerDraft.vue / EvidenceValidation.vue 这三个被工作区代码 import 的文件完全不在版本控制内。
- **风险**：两个具体爆点：(1) 一旦有人执行 `git clean -fd`、`git stash`（不带 -u 反而留脏树）或换一台机器，这 3 个未跟踪文件就消失，而 DiagnosisPanel.vue:2-3 与 useDiagnosisChat.js:5 仍然 import 它们，`vite build` 会直接以 `Could not resolve "./answerStream"` 失败，前端无法构建；(2) 仓库 HEAD 与线上 dist-prod 产物已经语义不一致——干净 clone 得到的是旧轮询实现，这段带断点续传的核心逻辑无法被 review、无法被复现、也无法回滚到已知版本。
- **业界做法**：主干开发 / 小步提交（trunk-based development），CI 的前置门禁必须是在干净 clone 上跑 `npm ci && npm run build`（甚至 `--frozen-lockfile`），保证「仓库状态 = 可构建状态」；功能改造与新组件应与删除旧实现放在同一个 commit/PR 里原子提交，避免出现「引用了不存在的模块」这种半迁移状态。
- **可采纳性**：easy
- **建议**：立刻 `git add frontend/src/features/diagnostics/answerStream.js frontend/src/features/diagnostics/components/AnswerDraft.vue frontend/src/features/diagnostics/components/EvidenceValidation.vue` 并把 parseAnswer.js 的删除一并提交（`git add -A frontend/src/features/diagnostics/`），使 HEAD 与工作区一致；随后在 CI 加一条 `npm ci && npm run build` 门禁（当前无 .github/workflows）。收益：消除「本地能跑、干净克隆不能跑」以及核心实现不可评审的状态。

### [medium] 422 校验错误被当作字符串提示，64 处会渲染成 [object Object]

- **证据**：全仓 `grep -rn "response?.data?.detail" src/ | wc -l` = 64，典型如 frontend/src/pages/LoginPage.vue:24 `message.error(e?.response?.data?.detail || '登录失败')`、frontend/src/pages/ApprovalsPage.vue:145、frontend/src/pages/MessagesPage.vue:416、frontend/src/features/monitoring/useMetricsPolling.js:15。而唯一正确的实现是 frontend/src/features/diagnostics/useDiagnosisChat.js:9-22 的 `extractErrorMessage()`（显式处理 `Array.isArray(detail)` 并映射 `item.msg`），只在 15 处使用。后端无自定义异常处理器（`grep -rn "RequestValidationError|exception_handler" backend/app` 无命中），因此 422 沿用 FastAPI 默认的 `{"detail":[{loc,msg,type},...]}` 结构。
- **风险**：任何 pydantic 校验失败（邮箱格式、端口类型、必填字段、scope 枚举）在前端只弹出一个 `[object Object]` 或空提示，用户完全不知道哪个字段错了——而本平台表单极多（服务登记、通知配置、Token scope、巡检间隔、重启策略），这类错误是最高频的交互路径。影响面是所有表单页，且因为提示无意义，用户会反复重试同样的错误输入。
- **业界做法**：把 HTTP 错误归一化收敛到一层：Axios 响应拦截器统一把 `detail` 数组映射为可读文案并附带字段路径，前端用 `form.setFields()` 做字段级错误回填（Ant Design Form 原生支持）；或用 TanStack Query 的全局 `onError` + 错误码到 i18n 文案的映射表。FastAPI 生态常见做法是后端注册 `RequestValidationError` handler 直接返回扁平化的可读 `detail` 字符串。
- **可采纳性**：easy
- **建议**：把 useDiagnosisChat.js:9-22 的 `extractErrorMessage` 提取为 `frontend/src/api/errorMessage.js` 并导出；在 frontend/src/api/index.js 的响应拦截器（:18-27）里统一调用它，把归一化后的可读消息挂到 `error.message`（或 `error.userMessage`）；然后将 64 处 `error?.response?.data?.detail || 'x'` 批量替换为 `error.userMessage || 'x'`。收益：一次性消除所有 [object Object] 提示，同时把错误文案口径统一。

### [medium] 轮询与筛选/翻页切换缺少请求取消，慢响应会覆盖新状态

- **证据**：frontend/src/pages/MessagesPage.vue:384-421 的 `loadMessages()` 用 `Promise.all` 拉 6 个接口，全程没有 AbortController、没有请求序号校验；:441-444 `watch(activeStatus)` 会立即重新 `loadMessages()`，:423-427 的翻页 `onMessagePageChange` 同样并发触发，两次请求的返回顺序不受控。frontend/src/App.vue:102 `unreadTimer = window.setInterval(loadUnreadMessages, 30000)` 无条件下每 30s 轮询且不判断 `document.hidden`；frontend/src/features/monitoring/useMetricsPolling.js:22-25 同样是固定 30s `setInterval`，无退避、无可见性判断、无错误退避。
- **风险**：(1) 快速切换消息状态标签（全部/未读/通知失败/分析失败…）或连续翻页时，先发出的慢响应后到会覆盖后发出的快响应，页面列表与实际筛选条件不一致——运维可能据此误判「这条告警已解决/已读」。(2) 在未读/审批等高频接口上，标签页挂后台仍持续打请求；如果页面同时监控 N 个系统的指标（useMetricsPolling 是按系统实例化的），请求量随系统数线性放大，会对后端与采集器造成持续无效负载。
- **业界做法**：请求可取消 + 结果归因是标准做法：把 `AbortController.signal` 传给 axios（`api.get(url, { signal })`），或在数据层迁移到 TanStack Query —— 它内建按 queryKey 去重、`cancelRefetch`、`staleTime`、`refetchOnWindowFocus`，能同时解决竞态与轮询失控。轮询侧业界惯例是「Page Visibility API 暂停 + 带 jitter 的指数退避」，避免 thundering herd。
- **可采纳性**：moderate
- **建议**：在 frontend/src/api/index.js 暴露一个极薄的 `withCancel()`（返回 `{ signal, cancel }`），MessagesPage.vue 与 MonitoringCenterPage.vue 的加载函数各自持有 controller，下一次调用前先 abort 上一次并丢弃已 abort 的结果；把 useMetricsPolling.js:22-25 与 App.vue:102 的 `setInterval` 换成「`document.hidden` 时跳过 + 失败后指数退避（30s→60s→120s 封顶）+ 重新可见时立即刷新一次」。收益：消除列表串档，并显著降低后台无效请求量。

### [medium] 生产接入凭证页的示例命令丢失域名：`??` 对空字符串不生效

- **证据**：frontend/src/features/system-detail/components/SystemTokensCard.vue:22 `const apiBase = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'`；frontend/.env.production:1-2 显式把 `VITE_API_BASE` 置为空（`VITE_API_BASE=`）。构建产物直接证实：dist-prod/assets/index-CcOD4EVE.js 中被内联为 `E="",q=M(()=>({log:`curl -X POST ${E}/openapi/v1/log-analysis ...` —— 空字符串不会被 `??` 回退，最终用户看到的是 `curl -X POST /openapi/v1/log-analysis`。同一页面附近的正确处理也在同一仓库：frontend/src/features/system-detail/components/CollectorDownloadCard.vue:30 用 `window.location.origin` 兜底。
- **风险**：「系统接入」页是第三方系统对接的唯一入口，用户复制出去的 6 段 curl 示例全部无主机名，在任何终端都必然失败（`Could not resolve host`），而且失败信息不会指向「示例命令错了」，接入方通常会先怀疑自己的网络或鉴权，从而显著拖长接入周期。这是纯前端可复现缺陷，与后端无关。
- **业界做法**：前端不硬编码 API 主机，统一取同源 `window.location.origin`，或采用运行时注入（`window.__ENV__`、启动时拉 `/config.json`）以支持「一套镜像多环境部署」，这也是 12-factor 里 build/release/run 分离的常规落法。
- **可采纳性**：easy
- **建议**：把 SystemTokensCard.vue:22 改为 `const apiBase = (import.meta.env.VITE_API_BASE?.trim() || window.location.origin).replace(/\/$/, '')`，与 CollectorDownloadCard.vue:30 的写法统一；顺手清理全仓剩余 `localhost:8000` 字面量（CollectorDownloadCard.vue:30 那个是 SSR 兜底、浏览器不可达，可保留）。收益：接入文档即刻可用，避免每个新接入方重复踩坑。

### [medium] 无 TypeScript、无 lint、无测试、无 CI，已出现 lint 能一秒捕获的真实缺陷

- **证据**：frontend/package.json:6-10 只有 `dev`/`build`/`preview` 三个脚本，没有 typecheck/lint/test；`find src -name "*.ts" | wc -l` = 0，`grep -rn 'lang="ts"' src | wc -l` = 0，无 tsconfig.json、无 eslint.config.* / .eslintrc*、无任何 *.spec.js/*.test.js、无 .github/workflows。真实缺陷样本：frontend/src/features/diagnostics/useDiagnosisChat.js:83 与 :92 在同一个对象字面量里重复定义 `totalTokens`（`totalTokens: report.total_tokens` 与 `totalTokens: report.total_tokens || 0`），这是 `no-dupe-keys` 级别的错误，ESLint 默认规则即可拦截。另外常量表已经出现重复定义：frontend/src/pages/MessagesPage.vue:246-278 的 `workflowStatusText/Color/ActionText` 与 frontend/src/pages/ApprovalsPage.vue:20-57 的 `ACTION_LABELS/STATUS_LABELS/STATUS_COLOR` 是同一份映射的两份拷贝。
- **风险**：前后端契约完全靠约定：`business_context.answer_stream`、`report.execution_result`、`data.items ?? data` 这类手写归一化散布在 40+ 文件里，后端字段改名或层级调整时前端不会报错，而是静默降级为「空列表/空字符串」——在运维场景里这等于「看起来一切正常」，误导性远大于报错。加上 17.8k 行无类型代码与两份漂移中的常量表，任何一次重构都要靠人工全量回归，交付速度会持续下滑。
- **业界做法**：Vue 3 + Vite 项目的标准组合是 `vue-tsc --noEmit` + ESLint（`eslint-plugin-vue` + `@typescript-eslint`）挂到 `lint-staged` 与 CI；接口类型由 OpenAPI 生成（`openapi-typescript` / `orval` / `openapi-generator`），保证契约变更时编译期报错。渐进迁移可用 JS + JSDoc `@typedef` + `checkJs` 打开类型检查而不必立刻重写。
- **可采纳性**：moderate
- **建议**：分三段：第一步零成本——加 `eslint` + `eslint-plugin-vue` + `vue-tsc`，在 package.json 增加 `"lint"`/`"typecheck"` 并在 CI 里先以 `--max-warnings=-1` 打底跑起来（立刻能抓到 :83/:92 的重复键与两份漂移常量表）；第二步把 `frontend/src/api/index.js` 与 useDiagnosisChat.js 的返回值补 JSDoc 契约类型并打开 `checkJs`；第三步按 feature 渐进迁 TS（先 features/diagnostics，它是数据形状最复杂的模块）。收益：把「静默降级」变成编译/CI 失败。

### [medium] 通知 Webhook URL 被当作非密文，明文回显在系统详情页

- **证据**：frontend/src/features/notifications/useNotifyConfig.js:41 `notifyCfg.webhook_url = notify.webhook_url || ''` 直接回填原文，而同一函数只对另外两个密钥做掩码判定：:35-36 `notifySecretAlreadySet.app = notify.app_secret === '***'` / `.smtp = notify.smtp_password === '***'`，`notifySecretAlreadySet` 里也根本没有 webhook 这一项。渲染侧 frontend/src/features/system-detail/components/NotifyConfigCard.vue:281 用的是普通 `<a-input>`（对比同文件 :255/:340 的密钥用 `<a-input-password>` + 「修改」按钮）。后端原样返回：backend/app/schemas/systems.py:28 `webhook_url: str = ""`。
- **风险**：飞书/钉钉/Slack 的群机器人 Webhook 是「持有即能用」的凭证，拿到就能向生产告警群发送任意内容（包括伪造告警、诱导误操作）。现在任何能打开该系统详情页的成员都能看到明文，并且它会出现在浏览器 DOM、截图、录屏、前端日志里；轮换也只能靠人工改配置，没有「已配置/重新输入」的 write-only 语义。
- **业界做法**：所有凭证一律 write-only：服务端只返回 `configured: true` 或统一掩码 `***`（与同文件 app_secret/smtp_password 的处理一致），需要轮换时走「重新输入」流程；密钥落 KMS/Vault（HashiCorp Vault、AWS Secrets Manager），接口层用 Pydantic `SecretStr` 做字段级脱敏，避免密钥出现在任何 GET 响应里。
- **可采纳性**：easy
- **建议**：在 useNotifyConfig.js 的 `notifySecretAlreadySet` 增加 `webhook`（:22），在 `syncNotifyFromSystem`（:28-50）对 `webhook_url` 做与 `app_secret` 相同的 `=== '***'` 判定并把输入框置空，存盘时同样在 `saveNotify`（:114-119）里以 `***` 回传原来的值；NotifyConfigCard.vue:281 换成 `<a-input-password>` + 「修改」按钮。后端 `GET /systems/{id}` 改为只回掩码。收益：与已有的密钥处理保持一致，消除一处凭证泄露面。

### [medium] 危险渲染函数在模板里直接调用，流式输出期间反复重解析全部历史 Markdown

- **证据**：frontend/src/features/diagnostics/useDiagnosisChat.js:425-428 的 `renderMd()` 每次执行都会跑完整的 `markedParser.parse()` + `DOMPurify.sanitize()`，且没有任何缓存；它被当成渲染表达式直接写在模板里：frontend/src/features/diagnostics/components/DiagnosisPanel.vue:287 `<span v-html="renderMd(message.text)" />`、:301 `<div v-else class="md" v-html="renderMd(message.text)" />`、frontend/src/features/diagnostics/components/AnswerDraft.vue:11 与 :13 同理（还把函数当 prop 传下去）。而 SSE 每来一个事件都会走 `deliver()` → `onReport` → 更新 `messages.value[index]`（answerStream.js:12-16,60-67），触发整个消息列表重渲染。
- **风险**：一次诊断会持续几十秒到十几分钟、期间产生几十到上百次事件；每次事件都会对列表里**每一条**历史消息重跑一遍 markdown 解析 + DOMPurify 净化，复杂度是 O(消息数 × 文本长度) 且随会话增长。长会话下会出现明显掉帧、滚动与输入卡顿，而这恰好发生在用户正在应急处置的时间窗内。
- **业界做法**：昂贵派生值必须缓存：Vue 用 `computed`/`shallowRef` 按消息 id 记忆化，或对 Markdown 渲染做 LRU 缓存；流式文本采用增量渲染（只重解析最后一个块）；长列表用 `v-memo` 或虚拟滚动（`vue-virtual-scroller`）控制渲染开销。业界聊天类控制台（如 Grafana Assistant、Sentry AI）普遍把「渲染后的 HTML」作为数据模型的一部分而不是渲染期计算。
- **可采纳性**：easy
- **建议**：在 useDiagnosisChat.js 里维护 `const renderedCache = new Map()`，`renderMd(text)` 先查缓存（key 用文本内容，命中直接返回），并对 cache 做容量上限（如 200 条）防内存增长；同时给 DiagnosisPanel.vue 的消息循环加 `v-memo="[message.text, message.status]"`，未变化的消息整块跳过 diff。收益：长会话的渲染耗时预期下降一个数量级。

### [low] 可访问性覆盖稀疏：移动抽屉缺对话框语义与焦点管理，图标按钮缺可读名称

- **证据**：全仓 `grep -rn "aria-|role=|alt=|tabindex" src/ | wc -l` 仅 21 处，对应约 17.8k 行代码。具体缺口：frontend/src/App.vue:126 `<div v-if="isMobile && drawerOpen" class="sidebar-backdrop" @click="closeDrawer" />` 没有 `role="dialog"`/`aria-modal`、没有 ESC 关闭（无 `keydown.esc`）、没有焦点转移与焦点归还；:223 `<button class="hamburger" @click="openDrawer">` 只有 SVG 没有 `aria-label` 也没有 `aria-expanded`；主题色点 :177-185 与 frontend/src/pages/LoginPage.vue:51-58 的 `<button>` 内容为空，只有 `:title`。正面例子也存在（DiagnosisPanel.vue:201,421,430-437 的 aria-label 做得不错），说明是覆盖不均而不是完全缺失。
- **风险**：键盘与读屏用户无法可靠地打开/关闭移动导航，也读不出图标按钮与主题色点的用途；对有键盘操作习惯的运维人员（终端重度用户）在应急场景下是实实在在的效率损失。目前无外部合规硬要求，因此定级 low，但成本极低。
- **业界做法**：遵循 WAI-ARIA Authoring Practices 的 Modal Dialog / Disclosure 模式：`role="dialog"` + `aria-modal="true"` + 焦点陷阱（`focus-trap`）+ ESC 关闭 + 关闭后焦点归还触发按钮；图标按钮统一 `aria-label`；CI 用 `axe-core`（`vitest-axe` 或 `@axe-core/playwright`）做无障碍回归，把「不退化」交给流水线而不是自觉。
- **可采纳性**：easy
- **建议**：改 frontend/src/App.vue：给 backdrop 加 `role="dialog" aria-modal="true" aria-label="导航菜单"`、在 onMounted 里注册 `keydown.esc` 关闭抽屉并在关闭后把焦点还给汉堡按钮；汉堡按钮补 `aria-label="打开导航菜单"` 与 `:aria-expanded="drawerOpen"`；App.vue:177-185 与 LoginPage.vue:51-58 的主题按钮补 `:aria-label="item.name"`。随后引入 `@axe-core/playwright` 在 CI 里跑一遍关键页面。收益：以很小改动覆盖键盘/读屏用户，并建立不退回的护栏。

### [low] 全站文案硬编码中文，无 i18n 层且日期格式化口径已不一致

- **证据**：frontend/package.json:11-24 依赖里没有 `vue-i18n` 或缺省语言包；frontend/index.html:2 固定 `lang="zh-CN"`；文案直接写在模板与逻辑里，例如 frontend/src/App.vue:19-47 的导航 `label/title`、frontend/src/pages/ApprovalsPage.vue:20-57 的四张标签映射表、frontend/src/composables/useOnboarding.js:23-66 的引导步骤文案，另有 60+ 处 `message.error('中文')`。日期格式已经开始分叉：frontend/src/pages/MonitoringCenterPage.vue:49 用 `toLocaleString('zh-CN', {...})`，而 frontend/src/features/system-detail/components/SystemTokensCard.vue:64 用不带 locale 的 `toLocaleString()`（跟随浏览器语言）。
- **风险**：一旦需要英文界面或跨区域交付，要改动 40+ 个文件且无法灰度发布；即便不出海，「同一页面出现两种日期格式」这类不一致也会降低控制台的专业度。属于可预见的技术债而非当前故障，故定级 low。
- **业界做法**：从第一天就以 `vue-i18n` 的 `$t()` + `locales/*.json` 组织文案（哪怕当前只有一种语言），时间统一走 `Intl.DateTimeFormat` 并显式传 locale/时区；CI 用 `i18n-ally` 或自建脚本检查缺失/未使用的 key，避免文案回归时漏翻。
- **可采纳性**：moderate
- **建议**：若近期没有出海计划，现状可以接受，但建议先把「会外发或出现在错误提示里」的文案（message.error 文案、通知正文、邮件模板、引导步骤）以及全部日期格式化收敛到 `src/i18n/`（哪怕只有 zh-CN 一份实现），统一用 `Intl.DateTimeFormat(locale, opts)`。这样将来接 vue-i18n 时只需把取文案的函数体换成 `$t`，不必全量重写模板。

### [low] 打包策略把 antd 全量归入单一 vendor chunk，首屏固定承担 217KB gzip

- **证据**：frontend/vite.config.js:27-51 的 `manualChunks` 把所有 `ant-design-vue` 与 `@ant-design/icons-vue` 无条件归入 `vendor-antd`；frontend/src/main.js:40-69 在入口静态注册 28 个组件 + `import 'ant-design-vue/dist/reset.css'`。产物体积实测：frontend/dist-prod/assets/vendor-antd-SuaSDl6L.js = 747KB（gzip 217KB），且 dist-prod/index.html 的 6 条 `modulepreload` 把它列为无条件预载；首屏 gzip 合计约 340KB+（vendor-antd 217 + misc 52 + vue 30 + index 29 + router/api 约 16）。正向对照：`marked` 走动态 import，产物 vendor-markdown 未进 preload 列表，说明懒加载策略本身是可用的。
- **风险**：首屏固定承担 217KB gzip 的组件库，弱网/移动端 TTI 明显变长；而且这是单调恶化的——每加一个 antd 组件都不会增加「按需」边界，只会让这个 chunk 继续变大。由于 Caddy 已开启 zstd/gzip 且资源有 hash 长缓存，二次访问体验尚可，因此定级 low。
- **业界做法**：按需自动引入是 Vue + antd 的标准做法：`unplugin-vue-components` 配 `AntDesignVueResolver` 自动导入模板中用到的组件与样式，删掉手写注册表；chunk 切分交给构建器（或 `vite-plugin-chunk-split`）按实际路由依赖自动分组，而不是手动把整个组件库钉进一个 vendor；再配 `vite-plugin-compression` 产出 brotli（Caddy 侧已开 zstd/gzip，可实测增量收益）。
- **可采纳性**：easy
- **建议**：引入 `unplugin-vue-components` + `AntDesignVueResolver`，删除 frontend/src/main.js:40-69 的手工注册数组与 frontend/vite.config.js:30-32 强制归并 antd 的规则，让 Rollup 依据实际引用切分；保留其余 vendor 分组（尤其不要动 marked 的动态 import 路径）。收益：首屏 JS 显著下降，且增长趋势从「线性叠加」变成「按页面分摊」。


## 对抗性复核

- **审批中心「批准执行」无二次确认，一次误点直接对生产系统下发处置动作** → confirmed（修正严重度：high）
  证据逐条核实为真。ApprovalsPage.vue:242-250 批准按钮确为裸 @click="decide(item, true)"，:131-148 decide() 内 :134 直接 POST /systems/{sid}/workflow/{id}/decision，:135 立刻 message.success('已批准并执行')，模板中无 Modal.confirm、无 a-popconfirm、无 disabled 门控。对照证据也成立：SystemDetailPage.vue:90-100 restartService 用 Modal.confirm（okButtonProps:{danger:true}），ServicesConfigCard.vue:145、SystemTokensCard.vue:187、SystemsPage.vue:154 删除类动作均有 a-popconfirm（grep 全仓确认）。服务端确认不可逆且立即执行：api/workflow.py:67 仅 require_operator，services/workflows/service.py:238-239 非 pending 即报错（无法撤销/二次决策），:267-288 置 approved 后内联 await resume_workflow → execution.py:193-222 真的跑 docker restart / systemctl restart。破坏力量级我实测后反而更强而非更弱：execution.py:18 的 _REDIS_CMD_ALLOWLIST 含 FLUSHDB/FLUSHALL，本地系统一次误点可清空 Redis 数据。少数被夸大的细节需修正：(1) actions.py:46-67 中 restart_container/restart_systemd 的 catalog risk 是 medium，只有 run_redis_command 是 high；(2) actions.py:113-131 会把远程系统的 run_redis_command 降级成 manual，所以『批准远程 Redis 命令即执行』不成立，只有 descriptor.local 的系统会真执行（但本地正是平台自身生产机）；(3) :160 的 actionKey 单值确有并发 loading 缺陷，但 Ant Design Button 的 loading 会拦截同按钮重复点击，故『连点同一张卡片』基本被缓解，真正的残留问题是飞行中可点其它卡片（属次要）。缓解措施（服务端 require_operator、service.py:242-243 按系统校验重启权限、record_audit_event 留痕）只防越权、不防误点。综合：最后一道人工闸门上的生产变更缺少二次确认，且同仓对同一操作（重启）在别处强制确认，属于实打实的控制缺失，high 恰当。

- **JWT 存 localStorage，且整条部署链路没有任何 CSP 与安全响应头** → overstated（修正严重度：medium）
  事实层全部核实为真，无一处编造：stores/auth.js:6/15/42 读写删 localStorage；api/index.js:12 拦截器直读 localStorage；router/index.js:39（我读到的是守卫内 const authed = !!localStorage.getItem('token')）确实绕过 store；frontend/index.html:1-13 无 CSP meta；deploy/frontend.nginx.conf:1-19 只有 proxy_pass，无任何安全头；Caddyfile:1-45 只有 Cache-Control/Pragma；我做了全仓 grep（排除 node_modules/.git），Content-Security-Policy/X-Frame-Options/Strict-Transport-Security/Referrer-Policy/X-Content-Type-Options 在非 .venv 路径下零命中，审计师的 grep 结论成立。夸大之处在定级与风险表述：(1) v-html 出口共 3 处（DiagnosisPanel.vue:287/301、AnswerDraft.vue:11/13）全部经 useDiagnosisChat.js:426 的 DOMPurify.sanitize，唯一另一处 App.vue:161 的 v-html 是 App.vue:49-57 的静态图标常量表，全仓 grep innerHTML 零命中，即当前无任何注入入口；这也说明审计师自己写的『可被利用但暂无已知入口』是自相矛盾的措辞——没有入口就不是可利用。(2) 后端 config.py:53 access_token_expire_minutes=60*24，即 24 小时而非『长期有效』；但同时确认无 refresh token、无 jti/吊销（core/security.py:112-116，grep blocklist/revoke/jti 零命中），这是真实弱点。(3) 应用无第三方脚本/CDN，同源单页，XSS 一旦发生 cookie 方案同样挡不住同源即时操作，httpOnly 只能限制离线复用的价值。定级 high 需要『可复现的接管路径』，此处只有纵深防御缺口（缺 CSP/nosniff/X-Frame-Options/HSTS + 无吊销），所以纠正为 medium，推荐的两步走（先落安全响应头，再评估 httpOnly+BFF）依然值得做。另有一条审计师没写、但影响定级的细节：HEAD 里已提交的 renderMd（git show HEAD:frontend/src/features/diagnostics/useDiagnosisChat.js:287-289）是 markedParser.parse(text) 原样输出、完全未净化，净化只存在于未提交工作区；而线上产物 frontend/dist-prod（时间戳 Oct 15 20:41）的 assets/vendor-misc-CljcQUya.js 内含 DOMPurify，说明被审的静态实现来自工作区。即该项的『已被净化』前提依赖未入库的改动，一旦按 HEAD 重建前端，高危应升为 high 甚至 critical——这一点比原报告更值得警惕。

- **本次 SSE 改造的核心文件未入库，仓库 HEAD 仍是旧的轮询实现** → 复核结果缺失（落盘截断），已由主审独立核实。
