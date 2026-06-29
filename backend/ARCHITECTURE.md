# Backend Architecture

## Directory Layout

```text
app/
├─ api/              # FastAPI routers and HTTP mapping
├─ agent/            # diagnosis / workflow runtime
├─ core/             # config, db, auth, shared deps
├─ models/           # SQLModel tables grouped by domain
├─ repositories/     # persistence helpers
├─ schemas/          # request / response / service DTOs
├─ services/         # business orchestration
└─ workers/          # Celery entrypoints
```

包入口约定：

- `app.api.ROUTERS` 作为统一 router 注册入口
- `app.services.*.__init__` 只暴露真实存在的稳定 service API
- `app.repositories.__init__` 只暴露查询 helpers，不暴露临时实现细节

## Module Rules

- `app/api/` 只处理 HTTP 协议、依赖注入、状态码和异常映射。
- `app/services/` 负责业务编排，不直接依赖其他 route 文件。
- `app/repositories/` 负责数据库查询和持久化。
- `app/models/`、`app/schemas/` 按领域拆分，禁止继续新增全局聚合实现文件。
- `app/agent/diagnostics/` 和 `app/agent/workflows/` 负责 Agent/Graph 运行时逻辑；上层通过明确入口模块调用。
- 新增逻辑优先放入已有领域模块，不要把临时逻辑直接堆进 `api/`、`workers/` 或单文件工具层。

## Import Direction

- `api -> services -> repositories/models`
- `services -> repositories/models/schemas`
- `repositories -> models`
- `agent -> services/models/core`

禁止反向依赖：

- `services` 不能引用 `api`
- `repositories` 不能引用 `services`
- `api` 不能引用其他 `api` 文件的私有 helper

## Cleanup Policy

- 删除兼容层前，先把仓库内引用全部切到新模块。
- 兼容 facade 只允许短期存在；一旦仓库内无引用，应直接移除。
- 新增测试优先覆盖 service/repository/agent facade，而不是继续给旧兼容入口补测试。
