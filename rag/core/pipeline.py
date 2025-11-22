# rag/core/pipeline.py
# -*- coding: utf-8 -*-
"""
RAGPipeline: 结合主记忆 + 辅助记忆 + 知识库（预留）
- run(): 给定 query，拼接上下文，调用 LLM，返回答案
"""
import json
import re
from typing import Dict, Any, List
from rag.datasource.base import Datasource
from rag.memory.memory_manager import MemoryManager
from rag.llm.providers.openai_client import OpenAIClient
from rag.core.retriever_jd import JDRetriever

class RAGPipeline:
    def __init__(self, ds: Datasource, memory: MemoryManager, llm: OpenAIClient):
        """
        :param ds: Datasource 实例（封装 minio / weaviate / registry / primary / contexts）
        :param memory: MemoryManager 实例
        :param llm: LLM 客户端（默认用 OpenAIClient，可换）
        """
        self.ds = ds
        self.memory = memory
        self.llm = llm

    def _fetch_texts(self, urls: List[str]) -> List[str]:
        """
        从 MinIO 拉取一批 url 对应的正文
        """
        texts = []
        for url in urls:
            try:
                texts.append(self.ds.minio.get_text(url))
            except Exception as e:
                texts.append(f"[读取失败: {url}, 错误: {e}]")
        return texts

    def run(
        self,
        memory_id: str,
        app: str,
        query: str,
        summary_k: int = 1,
        recent_k: int = 6,
        aux_top_k: int = 5,
        aux_threshold: float = None,
        max_chars: int = 4000,
    ) -> Dict[str, Any]:
        """
        这个函数是只结合记忆能力回答用户的问题
        执行一次完整的 RAG 推理：
        1) 调用 MemoryManager 获取上下文（主记忆 + 辅助记忆）
        2) 从 MinIO 拉取正文
        3) 拼接上下文（有 token/字符限制）
        4) 调用 LLM 生成回答

        :return: { "answer": str, "context_used": dict }
        """
        # 1) 从记忆模块获取上下文
        ctx = self.memory.get_context(
            memory_id=memory_id,
            app=app,
            query=query,
            summary_k=summary_k,
            recent_k=recent_k,
            aux_top_k=aux_top_k,
            aux_threshold=aux_threshold,
        )
        # print(ctx)

        # 2) 拉取摘要和最近消息的正文
        texts = []
        texts.extend(self._fetch_texts(ctx.get("summary_urls", [])))
        texts.extend(self._fetch_texts(ctx.get("recent_urls", [])))

        # 3) 加上辅助记忆检索到的内容
        for hit in ctx.get("retrieved", []):
            texts.append(hit["content"])

        # print(" texts", texts)
        # 4) 拼接上下文（加长度限制）
        context = ""
        for t in texts:
            if len(context) + len(t) > max_chars:
                break
            context += "\n\n" + t

        # 5) 构造 prompt 并调用 LLM
        prompt = (
            f"以下是与用户相关的历史对话与信息，请结合它们回答用户问题。\n"
            f"--- 上下文开始 ---\n{context}\n--- 上下文结束 ---\n\n"
            f"用户问题：{query}\n请用简洁、准确的方式回答。"
        )

        answer = self.llm.complete(
            prompt,
            temperature=0.3,
            top_p=1.0,
            max_tokens=800,
            system="你是一个严谨的助手，会结合历史上下文回答用户问题。"
        )

        return {
            "answer": answer,
            "context_used": ctx
        }

    def generate_interview_questions(
            self,
            memory_id: str,
            app: str,
            resume_url: str | None = None,
            jd_id: str | None = None,
            company: str | None = None,
            target_position: str | None = None,
            jd_top_k: int = 1,
            memory_top_k: int = 3,
            max_chars: int = 500,
            basic_count: int = 3,
            project_count: int = 3,
            scenario_count: int = 3,
    ):
        """
        面试官场景（改进版）：
        ------------------------------------------------
        基于候选人简历 + 岗位JD + 历史上下文，
        分三步生成三类问题（基础题 / 项目题 / 场景题），
        各自独立调用 LLM，再汇总成9道高质量面试题。
        """

        # 1️⃣ 拉取候选人简历内容
        resume_data = {}
        if resume_url:
            try:
                resume_text = self.ds.minio.get_text(resume_url)
                resume_data = json.loads(resume_text)
            except Exception as e:
                resume_data = {"error": f"读取简历失败: {str(e)}"}

        # 2️⃣ 获取记忆上下文
        ctx = self.memory.get_context(
            memory_id=memory_id,
            app=app,
            query=target_position or "面试生成",
            recent_k=memory_top_k,
            summary_k=1
        )
        texts = []
        texts.extend(self._fetch_texts(ctx.get("summary_urls", [])))
        texts.extend(self._fetch_texts(ctx.get("recent_urls", [])))
        for hit in ctx.get("retrieved", []):
            texts.append(hit["content"])
        context = "\n\n".join(texts)[:max_chars]

        # 3️⃣ 获取 JD 内容（优先用户上传的 JD）
        jd_context = ""
        if jd_id:
            try:
                row = self.ds.uploaded_jd.get(jd_id, memory_id)
                print("row:", row)
                if row:
                    company = row.get("company") or company  # ✅ 自动覆盖
                    target_position = row.get("position") or target_position
                    jd_context = row.get("content") or ""
                    print(f"✅ 使用用户上传的JD：{jd_id} ({company or ''} - {target_position or ''})")
                else:
                    jd_context = "[未找到上传的JD]"
                    print(f"⚠️ 未找到 jd_id={jd_id} 对应JD记录，回退至JD库检索。")
                    retriever = JDRetriever(collection="InterviewerJDKnowledge", company=company)
                    jd_hits = retriever.search(target_position or "通用面试", top_k=jd_top_k)
                    jd_context = "\n".join([
                        f"岗位要求：{h['requirements']}\n描述：{h['description']}"
                        for h in jd_hits
                    ])[:max_chars]
            except Exception as e:
                print(f"⚠️ 读取用户上传JD失败: {e}")
                jd_context = f"[JD读取失败: {e}]"
        else:
            # 🔁 原逻辑：JD向量库检索
            print("# 🔁 原逻辑：JD向量库检索")
            retriever = JDRetriever(collection="InterviewerJDKnowledge", company=company)
            jd_hits = retriever.search(target_position or "通用面试", top_k=jd_top_k)
            jd_context = "\n".join([
                f"岗位要求：{h['requirements']}\n描述：{h['description']}"
                for h in jd_hits
            ])[:max_chars]


        # 4️⃣ 通用基础信息块
        base_info = f"""
    职位：{resume_data.get('position', '')}
    技能：{', '.join(resume_data.get('skills', []))}
    项目经历：
    {chr(10).join(resume_data.get('projects', []))}

    历史面试上下文：
    {context}

    岗位信息（JD）：
    {jd_context}
    """

        # ---------------------------------------------------------------------
        # 5️⃣ 三类题型 Prompt 构造
        # ---------------------------------------------------------------------

        # (1) 基础知识与技能题
        basic_prompt = f"""
    你是一名资深技术面试官，请根据候选人简历中的技能列表，
    生成三道基础知识与技能类面试题。

    要求：
    - 每题必须直接基于候选人简历中提到的具体技术（如 SpringBoot、Redis、Kafka、Flink、Docker、MyBatis、WebSocket 等）；
    - 考察候选人对技术原理、架构设计、性能优化的理解；
    - 问题应具体、有深度；
    - 严格输出 JSON 格式，字段名为 "questions"。
    
    【候选人信息】
    {base_info}
    
    【输出示例】
        {{
          "questions": [
            "在你熟悉的 SpringBoot 技术栈中，系统如何同时集成 Redis、Kafka 和 RabbitMQ？请详细说明，在高并发下的消息一致性与事务保障机制",
            ...
          ]
        }}
    """


        # (3) 场景分析题
        scenario_prompt = f"""
    你是一名企业技术面试官，请根据岗位描述（JD）与候选人简历信息，
    生成三道场景分析题，用于考察候选人应对实际问题的能力。

    要求：
    - 每题描述一个真实业务场景,业务场景要足够详细清晰，让候选人分析问题与解决方案；
    - 问题应结合候选人项目背景与技术栈；
    - 严格输出 JSON 格式。

    【岗位信息（JD）】
    {jd_context}

    【候选人简历摘要】
    {base_info}
    
    【输出示例】
        {{
          "questions": [
            "假设你在“企业智能客服平台”项目中遇到如下场景：系统每日处理上百万次用户消息请求；WebSocket 长连接频繁断开；Redis 缓存命中率降低，响应时间上升。请分析：可能的性能瓶颈来源；如何在服务端架构上改进（包括连接管理、缓存策略与异步任务设计）；",
            ...
          ]
        }}
    """

        # ---------------------------------------------------------------------
        # 6️⃣ 三次独立调用 LLM
        # ---------------------------------------------------------------------
        def _ask_llm(prompt, temperature=0.4, max_tokens=600):
            return self._extract_questions(
                self.llm.complete(
                    prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system="你是一名面试官助手，只输出JSON格式的题目，不解释。"
                )
            )

        basic_questions = _ask_llm(basic_prompt, temperature=0.3)
        basic_questions = basic_questions[:basic_count]
        # 需要加入到 project prompt 里避免重复
        previous_basic = "\n".join([f"- {q}" for q in basic_questions])
        # (2) 项目经验题
        project_prompt = f"""
           你是一名资深系统架构面试官，请基于候选人的项目经历，
           生成 {project_count} 道项目深度问题。

           避免与以下基础题重复：
           {previous_basic}
           要求：
           - 每题必须引用项目名称；
           - 明确考察候选人项目中的设计思路、技术实现、性能优化或系统稳定性；
           - 优先结合简历中出现的具体技术（如 Flink、Kafka、Redis、WebSocket 等）；
           - 问题要足够详细、具体；
           - 严格输出 JSON 格式。

           【候选人项目经历】
           {base_info}

           【输出示例】
               {{
                 "questions": [
                   "在你负责的“智慧营销作业平台”项目中，你提到使用了 Flink 与 Kafka 进行实时数据流处理。请详细说明该系统的流式架构设计",
                   ...
               ]
               }}
           """

        project_questions = _ask_llm(project_prompt, temperature=0.5)
        project_questions = project_questions[:project_count]

        previous_basic_and_project = "\n".join(
            [f"- {q}" for q in (basic_questions + project_questions)]
        )
        # (3) 场景分析题
        scenario_prompt = f"""
            你是一名企业技术面试官，请根据岗位描述（JD）与候选人简历信息，
            生成 {scenario_count} 道场景分析题。
            
            避免与已有题目重复：
            {previous_basic_and_project}

            要求：
            - 每题描述一个真实业务场景,业务场景要足够详细清晰，让候选人分析问题与解决方案；
            - 问题应结合候选人项目背景与技术栈；
            - 严格输出 JSON 格式。

            【岗位信息（JD）】
            {jd_context}

            【候选人简历摘要】
            {base_info}

            【输出示例】
                {{
                  "questions": [
                    "假设你在“企业智能客服平台”项目中遇到如下场景：系统每日处理上百万次用户消息请求；WebSocket 长连接频繁断开；Redis 缓存命中率降低，响应时间上升。请分析：可能的性能瓶颈来源；如何在服务端架构上改进（包括连接管理、缓存策略与异步任务设计）；",
                    ...
                  ]
                }}
            """
        scenario_questions = _ask_llm(scenario_prompt, temperature=0.6)[:scenario_count]

        # ---------------------------------------------------------------------
        # 7️⃣ 汇总结果
        # ---------------------------------------------------------------------
        all_questions = basic_questions + project_questions + scenario_questions
        all_questions = [q for q in all_questions if q.strip()]  # 清理空项

        return {
            "questions": all_questions,
            "context_used": {
                "memory_context": ctx,
                "jd_context_preview": jd_context[:500],
                "resume_url": resume_url,
                "num_basic": len(basic_questions),
                "num_project": len(project_questions),
                "num_scenario": len(scenario_questions)
            }
        }

    def _extract_questions(self, text: str) -> list[str]:
        """解析 LLM 输出为题目列表（支持 JSON + 文本两种格式）"""
        text = text.strip()

        # ✅ 优先尝试 JSON 解析
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "questions" in data:
                q_list = data["questions"]
                if isinstance(q_list, list):
                    return [q.strip() for q in q_list if isinstance(q, str) and len(q.strip()) > 2]
        except Exception:
            pass

        # ⚙️ 回退方案：逐行提取（适配 markdown / 编号格式）
        lines = re.findall(r"[-•]?\s*\d*[\.\)]?\s*(.+)", text)
        clean = [l.strip() for l in lines if 4 < len(l.strip()) < 200]
        return clean[:10]  # 限制最多10个题目