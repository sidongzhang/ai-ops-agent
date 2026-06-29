# Frontend Architecture

## Directory Layout

```text
src/
├─ api/                 # axios client and auth/error interceptors
├─ features/
│  ├─ diagnostics/      # AI diagnosis panel + chat state
│  ├─ monitoring/       # metrics polling hooks
│  ├─ notifications/    # notify form state and save/test actions
│  ├─ services/         # service preset metadata / draft builders / payload builders
│  ├─ system-detail/    # system detail page and sub-panels
│  └─ systems/          # systems list page and create modal
├─ pages/               # top-level route pages shared across features
├─ router/              # route table and auth guard
└─ stores/              # global auth/theme stores
```

## Module Rules

- `pages/` 只负责页面入口，不承载复杂表单或重面板。
- `features/<domain>/components/` 放展示和交互组件。
- `features/<domain>/index.js` 作为 feature barrel，页面优先从目录入口导入。
- `features/services/` 只处理服务配置元数据和转换逻辑，不依赖页面组件。
- `api/` 只处理 HTTP 客户端，不掺杂页面状态。

## Import Direction

- `pages -> features -> api/stores`
- `feature components -> feature hooks/services -> api`
- `stores -> framework libs`

禁止反向依赖：

- `features` 不能依赖具体 `pages`
- `api` 不能依赖任何 `features`
- feature 之间避免直接互相 import 具体文件，优先走 `index.js`

## Performance Baseline

- 路由页面使用懒加载。
- `SystemDetailPage` 的监控、诊断、配置子面板按组件拆分并异步加载。
- `ant-design-vue` 已拆成 `core / form / data / icons` 多个 vendor chunk。
- `marked` 仅在 AI 诊断区首次进入时动态加载。
