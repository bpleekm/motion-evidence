# 代码风格

- Python 3.11语法基线，4空格缩进、UTF-8/LF，Ruff统一100列格式和导入顺序，版本固定在dev依赖。
- 模块/函数/变量用snake_case，类型用PascalCase，常量用UPPER_SNAKE_CASE。
- 命名表达业务角色：source_manifest、frame_catalog、review_task、decision_record、output_dir。纯区间运算中的局部起止索引可以使用简短名称。
- 模块按职责组织：产物与指纹、帧目录、区间运算、政策、任务规划、证据、OpenAI适配、输出校验及时间线合并。
- CLI仅解析和调度；缓存、媒体解码、API状态、业务校验各有明确边界。
- Pydantic结构及文件JSON字段属于协议；整理内部命名不随意改变帧ID、时间边界、规则或缓存版本语义。
- 注释解释为什么及关键约束，函数名表达做什么。敏感异常清洗与hold不是可删除的冗余分支。

```sh
ruff check --no-cache src tests tools
ruff format --check --no-cache src tests tools
```

每批整理后使用已有测试和跨仓库CLI联调验证。格式、命名和行为变更应分开审查；真实API配置缺失时保持hold，不能用模拟结果作为视觉质量结论。
