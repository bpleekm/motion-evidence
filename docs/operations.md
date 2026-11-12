# 运行与hold

Python>=3.11。venv/缓存/pytest/build目录统一外置，配置`TMPDIR`、`XDG_CACHE_HOME`、`PYTHONDONTWRITEBYTECODE=1`。源数据和检测结果是只读输入。

本地复制`.env.example`为`.env`并设为仅所有者可读写。配置OPENAI_API_KEY、OPENAI_MODEL；可选BASE_URL不携带凭据。加载器只读指定env文件，不借用其他session的shell密钥。密钥不进入命令行、仓库或日志。

prepare新建运行，review独立调用，merge可在没有API配置时先跑通，输出未决保留和hold。后续集中填写配置/补齐素材，重新review并merge；成功任务按当前版本跳过。不要编辑已准备的manifest/index/tasks；输入改变应建立新运行。

`holds.jsonl`为历史事件，`timeline.held_tasks`为当前未完成任务。先汇总缺失配置、输入、模型可用性和预算，再一次补齐。网络/API错误仅记录清洗后的类型，不保存可能包含凭据的原始异常。

prepare可用`--review-range START_MS:END_MS`加入已知问题；`--locks`加载规则一致的锁定范围。取证工具只能访问context内的实际帧，不执行模型给出的任意shell命令或文件路径。

Git只提交通用源码、schema、合成测试和文档。原片、图片、裁决、API请求、路径和机器信息不属于提交物。每次交付检查暂存内容后commit/push，身份按项目维护要求设置，不修改全局Git配置。
