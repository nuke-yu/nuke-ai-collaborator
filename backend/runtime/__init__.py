"""runtime — 进程拓扑层（Project-Cell Isolation V3）。

Supervisor / Worker / IPC / 路由 / 群生命周期。新增的"进程拓扑"关注点都在这里，
与现有领域模块（ai / executors / permissions …）正交。strangler-fig：本层纯增量，
现有单进程 app 在切换前照常运行。
"""
