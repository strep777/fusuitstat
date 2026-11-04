# 给开发模型与工程师的项目约定

本文件是仓库级上下文入口。**任何实现前先读它**，再按任务读取对应文档章节。

## 规则来源（唯一维护位置，不要另建第二份）

| 规则 | 文档 |
|---|---|
| 范围与需求 ID（FR-001–026 / NFR-01–09） | `文档/02_软件需求规格说明书.md` |
| 页面 URL 与状态 | `文档/03_站点路由与页面功能.md` |
| 模块边界与技术决策 | `文档/04_技术选型与架构设计.md` |
| 字段与数据库约束 | `文档/05_数据模型与一致性约束.md` |
| HTTP 契约与错误码 | `文档/06_API接口契约.md` |
| 委托状态机与名额 | `文档/07_委托报价与制作流程.md` |
| 钱、库存、付款与退款状态 | `文档/08_商城订单支付与退款.md` |
| 积分（星绒币）公式与反馈奖励 | `文档/09_积分与反馈功能规格.md` |
| 身份、安全与数据保留 | `文档/10_手机号登录安全与合规.md` |
| 素材与视觉规范 | `文档/11_界面交互与素材规范.md` |
| 业务最终选择（C01–C35） | `文档/功能确认清单-已确认.md` |
| 备份恢复与发布流程 | `文档/14_商业部署与运维手册.md` |

冲突处理顺序：用户已确认的最新决定 → 文档单一规则源 → 实际接口契约与迁移记录。
**代码不能通过改文档把缺陷变成预期行为。**

## 代码结构

```text
web/
  src/app/                   启动、路由组装、布局
  src/modules/<domain>/      pages、components、api、stores、types
  src/shared/ui/             无业务含义的控件
  src/shared/lib/            HTTP、格式化、可访问性、品牌 manifest
  admin.html                 后台独立入口（独立构建包）
server/
  app/platform/              settings、db、UoW、clock、ID、outbox、errors、security、audit
  app/modules/<domain>/      router / schemas / service / repository / models / ports
  app/modules/contracts.py   跨模块端口契约（唯一声明位置）
  app/application/workflows/ 跨模块编排
  app/adapters/{sms,mail,storage,payments}/  外部服务适配器
  app/worker.py              outbox 消费者与周期任务
  app/cli.py                 建库、种子、派生素材、备份、契约导出
  migrations/                Alembic 迁移
```

## 不可违反的工程约束

1. **模块边界**：模块只拥有自己的数据；不导入别的模块的 ORM 或 repository；跨模块只用 `ports.py` 与 `app/modules/contracts.py` 的 DTO，由 application workflow 编排。通用组件不得引用订单/积分枚举。
2. **金额**：一律整数分（`_cents`），币种显式；禁止 float、负数或超上限。价格、库存、权限、支付与积分由服务端裁定，前端只提交选择。
3. **事务**：同库强一致动作在一个 UnitOfWork 内完成；金额/库存争用用条件 UPDATE（`sa.update(...).where(条件)` + `rowcount`），**不要用 `SELECT FOR UPDATE`**（SQLite 不支持）。写业务记录与写 outbox 必须同一事务。
4. **幂等**：创建订单/支付/退款、接受报价、反馈提交、积分调整必须带幂等键；消费用 `event_id` + 消费者名去重，实现「至少一次投递、业务效果一次」。
5. **支付**：成功只认已验签通知或服务端主动查单；返回页、截图、前端回调不入账。超时返回 UNKNOWN 时用**同商户单号**查单，禁止换新单号再下单。
6. **安全**：手机号 HMAC 查找 + 加密保存；验证码只存 HMAC；会话只存哈希；写请求校验 CSRF 与 Origin；私密附件逐对象鉴权 + 短期签名 URL + `no-store`；日志禁止手机号、地址、验证码、cookie、签名 URL、完整支付报文。
7. **上传**：仅 JPEG/PNG/WebP，≤3 张、单张 ≤10MiB、边长 ≤8192、像素总量 ≤4000 万；校验扩展名/MIME/魔数/实际大小，安全解码后重新编码派生图并移除 EXIF；扫描完成前不可引用。
8. **前端**：只按 `error.code` 分支，不对 message 做逻辑判断；每个数据页面必须有 loading/empty/error/forbidden/not-found；表单还要 dirty/saving/saved/validation-error/session-expired；禁用按钮旁说明原因；不靠颜色单独表达状态。
9. **素材**：加载=77_双曜弈兽，积分=62_弈环双尾，Logo=35_逐星绒龙（深色面用白色版）。**必须通过 manifest 引用**（`web/src/shared/lib/brand-assets.ts`），不用 emoji 或随意生成图代替；原图不修改、不移动。
10. **品牌与文案**：品牌「弈星纪元兽装工作室」，经营主体「弈星纪元（上海）文化发展有限公司」，域名 `starium.cn`，积分名称「星绒币」。不得编造资历、客户评价、竞品数据或未核准交期；参考站文案、Logo、人物照片不得下载使用。
11. **功能开关**：未启用或未达商业门槛的能力保持关闭（默认 `false`），入口隐藏且后端拒绝（`FEATURE_DISABLED`），不存在绕过开关的 API。**不生成伪成功页。**
12. **测试**：不删测试、不关闭类型/签名/权限校验来让测试通过；沙盒适配器必须明确标记，不得冒充支付或短信已集成。

## 本地命令

```bash
# 后端
cd server
uv sync
uv run python -m app.cli init-db
uv run python -m app.cli seed-demo
uv run python -m app.cli create-owner --login owner --name "工作室负责人"
uv run uvicorn app.main:app --reload --port 8000
uv run pytest -q
uv run ruff check app tests migrations

# 前端
cd web
pnpm install
pnpm assets:derive      # 派生品牌素材（先校验原图 SHA256）
pnpm dev
pnpm typecheck && pnpm test && pnpm build
```

Windows 本地注意事项：

- `uv` 的缓存目录若不可写，设置 `UV_CACHE_DIR` 到仓库内（`.uv-cache/` 已忽略）。
- `/api` 由 Vite 代理到 `127.0.0.1:8000`；生产由 Nginx 同域转发。
- 开发环境短信与支付使用沙盒适配器：验证码写入 `server/var/storage/sandbox/sms.log`，登录接口在非生产环境会返回 `dev_hint` 便于联调；**生产环境不存在万能码、测试白名单或把验证码写进响应**。

## 每轮结束必须维护

`文档/17_交付检查与后续记录.md` 追加一条：

```text
日期/版本：
增量/任务 ID：
已实现的 FR：
修改文件：
接口/迁移变化：
测试命令与结果、证据位置：
未验证项或缺陷：
已决定的 Cxx 与决定来源：
下一步最小动作：
```

任务未完成时报告 BLOCKED/PARTIAL，**不生成虚假的 PASS**。未决事项记入 `文档/功能确认清单-已确认.md` 的待补行，不新建分散的待确认文档。
