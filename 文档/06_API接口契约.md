# API 接口契约

设计基线：REST `/api/v1`、JSON UTF-8、TLS。同域客户端使用 HttpOnly 会话 cookie，非 GET 写操作须 CSRF 校验；支付通知单独走渠道验签，不要求用户 cookie。实现后从 FastAPI 导出 OpenAPI 至 `contracts/openapi.json` 并生成前端类型；当前文档不是已运行接口。

## 通用协议

成功资源直接返回 DTO，列表使用 `items、next_cursor、has_more`。分页 `limit` 默认 20、最大 100，游标不可绕过用户隔离。静态内容可用 page 页码；同一资源不要混用两种分页。

错误统一形状：

```json
{"error":{"code":"QUOTE_EXPIRED","message":"报价已过期，请重新申请。","fields":{},"trace_id":"trace_example"}}
```

HTTP：400 格式错误，401 未认证，403 无权限/CSRF，404 无可访问资源，409 版本/状态/幂等冲突，422 字段校验，429 限流并含 Retry-After，503 暂时不可用。框架 422 也转换到统一形状。禁止把堆栈、手机号或支付原始报文返回用户。

幂等键用于创建订单/支付/退款、接受报价、反馈提交、积分调整。`If-Match` 或 `version` 用于内容与业务编辑，版本不符返回 409。用户 ID 从会话获取，不能信任 body.user_id。

## 接口目录

表中“本人”都包含逐资源归属检查；“员工”还需对应权限码。路径 ID 在查询 SQL 中与 owner 一同过滤。

| 方法与路径 | 权限 | 行为 |
|---|---|---|
| GET `/session/csrf` | 访客/本人 | 发放短期匿名或当前会话 CSRF token，no-store |
| POST `/auth/sms/challenges` | 访客+限流 | 创建 purpose=login 的验证码挑战 |
| POST `/auth/sms/verify` | 访客+CSRF | 原子消费验证码，登录/建号，设置会话 |
| POST `/auth/logout` | 本人 | 撤销当前会话并清 cookie |
| GET `/me`、PATCH `/me` | 本人 | 资料读取和昵称/头像更新 |
| GET `/me/sessions`、DELETE `/me/sessions/:id` | 本人 | 查看/撤销自己的会话 |
| POST `/me/phone-change/challenges`、POST `/me/phone-change/confirm` | 本人+二次验证 | 旧号/替代核验与新号验证；服务端原子换绑 |
| POST `/me/closure-requests` | 本人+二次验证 | 注销申请，返回需处理的合法阻碍 |
| GET/POST `/me/addresses`、PATCH/DELETE `/me/addresses/:id` | 本人 | 地址 CRUD；删除不改订单快照 |
| GET `/content/:kind`、GET `/content/:kind/:slug` | 公开 | 仅已发布；作品、公告、系列、FAQ |
| GET `/products`、GET `/products/:slug` | 公开 | SKU、可售展示、规格 |
| GET `/characters`、GET `/characters/:slug` | 公开 | 角色过滤、授权信息与状态 |
| GET `/schedule-rounds` | 公开 | 开放时间、匿名余量摘要 |
| POST `/uploads` | 本人/员工+限流 | 建临时上传，返回受限地址/表单 |
| POST `/uploads/:id/complete`、GET `/uploads/:id` | 文件所有者 | 核验实际对象并排队扫描/查询结果 |
| GET `/media/:id/access` | 授权主体 | 私密附件短期下载地址；no-store |
| GET/POST `/inquiries`、GET/PATCH `/inquiries/:id` | 本人 | 询价列表/草稿/允许版本的编辑 |
| POST `/inquiries/:id/submit`、`/withdraw`、`/supplements` | 本人 | 受状态约束动作 |
| GET `/inquiries/:id/quotes` | 本人 | 报价版本列表 |
| POST `/quotes/:id/accept` | 本人+幂等 | 确认版本/条款并建立唯一订单 |
| POST `/schedule-rounds/:id/applications` | 本人+幂等 | 普通申请或已启用限时申请 |
| GET `/commission-projects/:id` | 本人/分配装师 | 制作信息 |
| POST `/commission-projects/:id/measurements` | 本人 | 私密量体版本 |
| POST `/production-stages/:id/confirm`、`/changes` | 本人+版本 | 确认阶段或请求修改 |
| GET `/cart`、PUT `/cart/items/:skuId`、DELETE `/cart/items/:skuId` | 本人 | 服务端购物车；PUT 为绝对数量 |
| POST `/checkout/preview` | 本人 | 预览权威价格和配送 |
| POST `/orders`、GET `/orders`、GET `/orders/:id` | 本人 | 创建订单和查询 |
| POST `/orders/:id/cancel`、`/confirm-receipt` | 本人+状态 | 关闭核对或确认收货 |
| POST `/installments/:id/payment-attempts` | 本人+幂等 | 创建可支付参数 |
| GET `/payment-attempts/:id` | 本人 | 本地权威状态；按策略触发受限查单 |
| POST `/payment-notifications/wechat`、`/alipay` | 渠道验签 | 回调并持久化业务效果 |
| POST `/orders/:id/after-sales`、GET `/after-sales/:id` | 本人 | 创建售后/查看处理 |
| POST `/after-sales/:id/messages`、`/return-shipment` | 本人 | 补件、填写退货物流 |
| POST `/orders/:id/invoices` | 本人 | 开票申请 |
| GET `/points/account`、GET `/points/ledger` | 本人 | 余额和可解释明细 |
| POST `/feedback`、GET `/feedback`、GET `/feedback/:id` | 本人 | 反馈创建与查询 |
| POST `/feedback/:id/messages`、`/appeals` | 本人 | 补件/申诉 |
| GET `/notifications`、POST `/notifications/:id/read` | 本人 | 通知查询和已读 |

`/admin` API 组依权限实施：`/auth/login、/auth/mfa、/content、/products、/characters、/quotes、/schedule-rounds、/commission-projects、/orders、/shipments、/refunds、/reconciliation-runs、/feedback、/point-adjustments、/users、/staff、/settings、/audit-logs、/jobs`。列表 GET、详情 GET、编辑 PATCH；发布/批准/发货/重试分别用 POST 动作，不让通用 PATCH 越过状态机。

必须单独定义：`POST /admin/inquiries/:id/quotes` 发布报价；`POST /admin/refunds` 申请退款；`POST /admin/refunds/:id/approve` 批准；`POST /admin/feedback/:id/reviews` 审核；`POST /admin/point-adjustments/:id/approve` 调整批准。以上均需幂等、权限、原因和审计。

## 关键请求与响应

### 手机验证码

`POST /auth/sms/challenges` 输入 `{phone:"+8613800000000",purpose:"login",captcha_token:"..."}`。验证码不在 API 返回。统一响应 `{challenge_id,expires_in:300,retry_after:60}`，不暴露手机是否注册。短信通道失败给统一可恢复错误；对可疑目标不给注册枚举线索。

`POST /auth/sms/verify` 输入 `{challenge_id,phone,code,terms_version,privacy_version,accepted:true}`。code 为 6 位字符串保留前导零。返回 `{user:{id,nickname,phone_masked},session_expires_at}`，Set-Cookie 下发随机会话标识，响应体无长期 bearer token。

### 询价草稿与报价接受

```json
{"character_name":"示例角色","species":"龙","series_id":"series_example","suit_type":"full","design_asset_ids":["asset_example"],"requirements":"需要可拆卸尾巴","delivery_region":"CN-31","rights_declaration":true,"version":1}
```

提交时校验附件 ready、本人所有且 purpose=design，提交成功返回 inquiry_id、status、version、submitted_at。接受报价输入 `{quote_version:2,terms_version:"commission-1",accepted:true}`；返回 201 `{order_id,installment_id,amount_cents,currency,expires_at}`。重复请求返回同订单。

### 结算与订单

预览输入 `{items:[{sku_id,quantity}],address_id,coupon_code:null}`，响应 `{checkout_token,expires_at,lines,goods_cents,discount_cents,shipping_cents,total_cents,currency:"CNY"}`。

创建订单输入 `{checkout_token,address_id,terms_version,accepted:true}` 并带 `Idempotency-Key`。成功返回 `{order_id,order_no,status:"PENDING_PAYMENT",installment_id,total_cents,currency,expires_at}`。金额不再作为可信输入接收。

### 支付

输入 `{provider:"wechat",scene:"native"}`，不接收商户号或金额。响应 `{payment_attempt_id,status,action:{type:"qr",url:"..."},expires_at}`；scene 可支持 h5/jsapi，action 内容取决于真实已开通渠道。外部跳转目标由 adapter 白名单验证。

查询返回 `{id,status,order_id,verified_at}`，前端轮询初始 2 秒，逐步退避最大 15 秒，页面不可见暂停；到期提示“结果确认中/重新查询”，不能把超时渲染成已取消。

### 反馈与积分

反馈输入 `{category:"bug",title,body,page_path,steps,asset_ids}`：title 1–100 字、body 10–5000 字、附件最多 3 张、page_path 仅本站路径且去 query 敏感值。响应 `{id,status:"SUBMITTED",reward_status:"PENDING_REVIEW"}`。不能在提交响应里发确定积分。

积分返回 `{posted_balance,reserved_points,available_points,pending_points,rules_version}`。流水含 delta、业务中文描述、来源摘要、发生时间、调整关联和申诉入口；不能泄露他人反馈内容。

## 稳定业务错误码

`AUTH_REQUIRED、CSRF_INVALID、SMS_RATE_LIMITED、SMS_CODE_INVALID、SMS_CODE_EXPIRED、SMS_CHALLENGE_USED、RESOURCE_NOT_FOUND、VERSION_CONFLICT、STATE_CONFLICT、IDEMPOTENCY_CONFLICT、UPLOAD_NOT_READY、QUOTE_EXPIRED、QUOTE_CHANGED、SCHEDULE_CLOSED、NO_CAPACITY、STOCK_NOT_ENOUGH、PRICE_CHANGED、PAYMENT_PENDING、PAYMENT_UNKNOWN、REFUND_LIMIT_EXCEEDED、POINTS_INSUFFICIENT、FEEDBACK_DUPLICATE、PROVIDER_UNAVAILABLE`。

前端根据 code 展示已定义行为，不对 message 字符串做逻辑分支。人工可读 message 不包含 SQL、内部路径和第三方原始响应。

## 契约落地要求

每个 endpoint 必须有 operationId、请求 schema、成功/失败 schema、权限、幂等策略、限流策略、审计事件与契约测试。枚举从后端生成，前端禁手抄多份。I0 创建契约骨架；每个增量随实现补全 OpenAPI，CI 检查破坏性变更和生成类型漂移。
