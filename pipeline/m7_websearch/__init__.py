"""M7 联网检索适配层（蓝图第十六/十七条定案，T5 工单）。

可插拔适配层：统一结果 schema（schema.WebResult）+ 适配器注册框架（registry）
+ 关键词云端英译降级链（translate）+ web_search 节 Key 加载（config）。

新增检索源 = 实现 SearchAdapter 接口并注册，不改框架代码（T5 A/C 1）；
真实源适配器（OpenAlex/DOAJ/arXiv/千帆智能搜索生成）在 T6/T7 落地。
"""
