写时复制沙箱Agent 自由探索。FORK DATABASE 秒级完成整库快照 — 零数据拷贝。Agent 在沙箱中自由实验（写入、查询、甚至破坏表结构均可）；随后 MERGE TABLE 将成果合并回主线，或 DROP DATABASE 一键丢弃。内核级 COW 实现，而非应用层的保存/恢复。 源码: tools/deploy/mysql_test/test_suite/fork_table/
