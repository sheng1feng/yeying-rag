
# RAG 服务接口文档
## 如何启动服务
如何启动服务

1.构建 Docker 镜像：

 docker build -t yeying-rag:latest -f infra/Dockerfile .

启动 Docker 容器：

使用以下命令启动容器，映射本地端口和容器端口：

docker run -d --name yeying-rag --env-file .env --network host yeying-rag:latest

这将会把容器的 8001 端口映射到主机的 8001 端口。 


## 1. 健康检查接口：`/health`

### 功能
该接口用于检查服务的健康状态，包括服务版本、环境设置，以及外部依赖（如 Weaviate 和 MinIO）的健康状况。

### 请求方法
`GET`

### 请求示例
```bash
curl -X GET http://localhost:5000/health
```

### 响应示例
```json
{
  "service": "yeying-rag",
  "version": "0.1.0",
  "env": "dev",
  "time": "2025-10-19T12:39:20.197659Z",
  "dependencies": {
    "weaviate": {
      "status": "ok",
      "details": "Weaviate is up and running."
    },
    "minio": {
      "status": "ok",
      "details": "MinIO is operational."
    }
  }
}
```

### 说明
- **Weaviate** 和 **MinIO** 的状态会根据服务配置的健康检查结果返回，若服务未启用，则显示为 `"disabled"`。

---

## 2. 创建记忆接口：`/memory/create`

### 功能
用于创建新的记忆空间，返回 `memory_id`，为后续的推送和查询消息提供一个唯一标识符。

### 请求方法
`POST`

### 请求参数
- `app`: 应用名（如：`test_app`）。
- `params`: 包含摘要和检索的参数（如 `summary_every_n`, `max_summary_tokens`, `top_k`）。

### 请求示例
```bash
curl -X POST http://localhost:5000/memory/create -H "Content-Type: application/json" -d '{
  "app": "test_app",
  "params": {
    "summary_every_n": 3,
    "max_summary_tokens": 500,
    "top_k": 5
  }
}'
```

### 响应示例
```json
{
  "memory_id": "test_app_e25fac3b08d2"
}
```

### 说明
- 创建记忆空间后，`memory_id` 将作为后续操作（如推送和查询）中的标识符。

---

## 3. 推送消息接口：`/memory/push`

### 功能
向指定的记忆空间推送消息，用于存储对话或其他数据。

### 请求方法
`POST`

### 请求参数
- `memory_id`: 记忆空间 ID。
- `app`: 应用名称。
- `url`: 消息的 URL 或唯一标识符。
- `description`: 消息内容。

### 请求示例
```bash
curl -X POST http://localhost:5000/memory/push -H "Content-Type: application/json" -d '{
  "memory_id": "test_app_e25fac3b08d2",
  "app": "test_app",
  "url": "http://example.com",
  "description": "This is a test message."
}'
```

### 响应示例
```json
{
  "status": "ok",
  "row": 1
}
```

### 说明
- 消息成功推送后，返回 `status: "ok"` 和推送的行数（`row`）。

---

## 4. 查询记忆接口：`/query`

### 功能
查询指定记忆空间中的数据，根据不同模式返回问答或生成面试题。

### 请求方法
`POST`

## ⚙️ 请求体参数

| 字段 | 类型 | 必填 | 说明                                   |
|------|------|------|--------------------------------------|
| `app` | `str` | ✅ | 应用模式，取值 `default` 或 `interviewer`    |
| `memory_id` | `str` | ✅ | 记忆空间 ID                              |
| `query` | `str` | ❌ | 用户查询内容（`default` 模式必填）               |
| `resume_url` | `str` | ⚙️ | 简历路径（MinIO URL，仅 `interviewer` 模式使用） |
| `jd_id` | `str` | ⚙️ | 自定义 JD ID（从 `/query/uploadJD` 返回）    |
| `company` | `str` | ❌ | 公司名称（无 jd_id 时用于 JD 检索）              |
| `target_position` | `str` | ❌ | 岗位名称（无 jd_id 时用于 JD 检索）              |
| `jd_top_k` | `int` | ❌ | JD 检索数量（默认 2）                        |
| `memory_top_k` | `int` | ❌ | 记忆检索数量（默认 3）                         |
| `max_chars` | `int` | ❌ | 拼接上下文最大长度（默认 4000）                   |
| `basic_count` | `int` | ❌ | 基础知识题数量（默认 3） 
| `project_count` | `int` | ❌ | 项目深度题数量（默认 3） 
| `scenario_count` | `int` | ❌ | 场景分析题数量（默认 3） 
---

## 📘 一、普通问答模式（`app=default`）

### 请求示例
```bash
curl -X POST http://localhost:8001/query \
-H "Content-Type: application/json" \
-d '{
  "app": "default",
  "memory_id": "qa_001",
  "query": "Explain how self-attention works in a transformer model."
}'
```

### 响应示例
```json
{
  "answer": "The capital of France is Paris.",
  "context_used": "Some context data used to generate the answer."
}
```

## 📘 二、面试官模式（app=interviewer）
面试官模式通过简历（resume_url）与岗位 JD（jd_id 或 JD 检索）生成高质量定制化面试题。
### 说明
- 在 `app = "interviewer"` 模式下，接口会生成面试题并返回相关的面试问题；在默认模式下，提供一般的问答功能。

### 场景 1：使用自定义 JD（推荐） 
#### 请求示例
```
curl -X POST http://localhost:8001/query \
-H "Content-Type: application/json" \
-d '{
  "app": "interviewer",
  "memory_id": "interv-001",
  "resume_url": "resume/zhangsan.json",
  "jd_id": "b5c1e8c4-bf61-4c8a-bc5b-7c2834a26d1c"
  "basic_count": 3,
  "project_count": 3,
  "scenario_count": 3
}'
```
#### 响应示例
```
{
  "questions": [
    "请详细说明你在项目中如何设计 Redis 缓存层来应对高并发请求？",
    "在 Kafka 消息队列使用中，你如何保证消息有序性？",
    "结合项目经验，谈谈分布式系统中一致性与性能的平衡。"
  ],
  "context_used": {
    "jd_context_preview": "岗位要求：负责微服务架构设计与优化；任职要求：熟悉SpringBoot、Redis、Kafka等。"
  }
}
```
#### 行为逻辑

系统根据 jd_id 从 SQLite 表 user_uploaded_jd 读取 JD；
读取成功后覆盖 company、target_position；
综合简历 + JD 内容生成三类问题（基础 / 项目 / 场景），共 9 道。

### 场景 2：未上传 JD，自动从向量库检索
#### 请求示例
```
curl -X POST http://localhost:8001/query \
-H "Content-Type: application/json" \
-d '{
  "app": "interviewer",
  "memory_id": "interv-002",
  "resume_url": "resume/lisi.json",
  "company": "字节跳动",
  "target_position": "后端开发工程师"
  "basic_count": 3,
  "project_count": 3,
  "scenario_count": 3
}'
```
#### 响应示例
```
{
  "questions": [
    "请解释分布式存储系统中一致性哈希的作用。",
    "在数据调度与抓取流程中，你如何提升系统可靠性？",
    "请描述一次你解决高并发性能瓶颈的经历。"
  ],
  "context_used": {
    "jd_context_preview": "岗位要求：负责后端服务开发与优化..."
  }
}

```
#### 行为逻辑

若未提供 jd_id，则从 Weaviate JD 向量库中检索相似岗位；
拼接岗位描述 + 简历信息；
生成 9 道问题。


## 5. 上传 JD 接口：/query/uploadJD

### 功能
面试官上传自定义 JD，生成 jd_id 用于后续面试题生成。

### 请求方法
`POST`

#### 请求体
```
{
  "app": "interviewer",
  "memory_id": "interv-001",
  "company": "阿里巴巴",
  "position": "后端开发工程师",
  "content": "岗位职责：负责微服务架构设计与优化；任职要求：熟悉SpringBoot、Redis、Kafka等。"
}
```
#### 响应
```
{
  "jd_id": "b5c1e8c4-bf61-4c8a-bc5b-7c2834a26d1c",
  "message": "JD 上传成功"
}

```

####  校验逻辑

仅当 app="interviewer" 时允许上传；
memory_id 必须存在于 mem_registry 表；
成功上传后写入 SQLite 表 user_uploaded_jd：


## interviewer 运行流程总览
```
/memory/create     → 创建 interviewer 记忆空间
/query/uploadJD    → 上传自定义 JD，返回 jd_id
/query             → 使用简历 + jd_id 生成定制面试题
```
当 jd_id 存在时，系统直接使用上传的 JD；
当 jd_id 缺失时，自动从 JD 向量库检索相似岗位内容。


2025-11-08
---
