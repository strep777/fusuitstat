"""委托模块：询价、报价、档期名额、制作单、量体、制作阶段与变更单。

拥有的数据：inquiries、inquiry_revisions、quotes、quote_items、schedule_rounds、
schedule_applications、slot_reservations、commission_projects、measurements、
production_stages、change_requests。
不读订单、付款与积分表（04 文档）；跨模块协作只通过 ``ports.py``。
"""

from __future__ import annotations

__all__: list[str] = []
