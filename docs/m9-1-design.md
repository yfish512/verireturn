# M9.1：会话命令串行化与恢复

同一会话的消息、确认、取消会修改同一个任务槽位和业务命令。M9.1 在 Agent Runtime 的整个处理窗口持有以 `thread_id` 派生的锁：PostgreSQL 使用 session-level advisory lock，SQLite 开发与单进程测试使用等价的进程内锁。

锁覆盖意图解析、槽位合并、受控工具调用、确认恢复和回复持久化。同一会话的并发确认会在获取锁后重新读取确认状态，因此只有一个请求能调用确认业务接口；其他请求得到 `CONFIRMATION_ALREADY_RESOLVED`。不同会话不互相阻塞。

锁不替代业务接口的幂等键：所有写业务调用仍使用稳定 idempotency key。两者分别应对并发竞态与网络重试。
