"""
FastAPI web service for Aira Workflow.
Provides streaming and non-streaming endpoints for workflow execution.
"""

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from pydantic import BaseModel, Field

# from sse_starlette.sse import EventSourceResponse
from starlette.status import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR

from api.auth_validator import validate_product_match_token, validate_token_and_get_wf

# Import HITL API router
from api.broker_hitl_api import router as broker_hitl_router

# from storage.mysql.message_storage import MessageStorage
# from storage.redis.redis_manager import RedisClusterManager
from cache.redis_manager import RedisClusterManager

# from starlette.responses import StreamingResponse
from config import (
    get_logger,
)
from config import (
    request_id as request_id_ctx,
)
from config import (
    trace_info as trace_info_ctx,
)
from configs.config_manager import ConfigManager
from constants import (
    RESPONSE_MODE_BLOCKING,
    RESPONSE_MODE_STREAMING,
    UPSTREAM_SPAN_ID_HEADER_NAME,
    UPSTREAM_TRACE_ID_HEADER_NAME,
    WF_REQUEST_ID_HEADER_NAME,
    WF_TYPE_CHATFLOW,
    WF_TYPE_WORKFLOW,
)
from db.database import Database
from errors import StateValidationError, WorkflowError
from monitor.memory_monitor import get_memory_monitor
from state import BaseState
from tool.env_loader import load_env
from workflow.base_workflow import BaseWorkflow
from workflow.services.chatflow_generate_service import ChatflowGenerateService
from workflow.services.workflow_generate_service import WorkflowGenerateService
from service.product_name_matcher import match_product_name

logger = get_logger(__name__)

# Langfuse 初始化（在 lifespan 中完成）
langfuse = None
langfuse_handler = None


# Pydantic models for request/response
class WorkflowInput(BaseModel):
    """Input model for workflow execution."""

    query: str = Field(default="", description="用户查询内容")
    conversation_id: Optional[str] = Field(default=None, description="会话ID")
    user: str = Field(..., description="用户ID")

    response_mode: Optional[str] = Field(default="streaming", description="响应模式")
    
    scene_type: Optional[str] = Field(default=None, description="场景类型")
    
    agent_id: Optional[str] = Field(default=None, description="智能体ID")

    sys_app_id: str = Field(default="aira-workflow", description="应用ID")
    sys_workflow_id: str = Field(default="1745215322322", description="工作流ID")

    files: Optional[List[Dict]] = Field(default=None, description="上传文件")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="输入变量")


class ProductMatchRequest(BaseModel):
    """产品名称匹配请求。"""

    input_product: str = Field(..., description="待匹配的产品描述或名称")


class StreamChunk(BaseModel):
    """Streaming response chunk model."""

    chunk_id: str = Field(..., description="分块ID")
    type: str = Field(..., description="分块类型: 'message'|'update'|'error'|'done'")
    data: Dict[str, Any] = Field(..., description="分块数据")
    timestamp: str = Field(..., description="时间戳")
    

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 加载环境变量
    load_env()

    # ========== ConfigManager 初始化 ==========
    config_manager = ConfigManager(
        local_cache_dir=Path("configs/cache"),
        server_address=os.getenv("CONFIG_SERVER_ADDRESS"),
        namespace=os.getenv("CONFIG_NAMESPACE"),
    )
    await config_manager.initialize()
    app.state.config_manager = config_manager

    # 预加载 esp_broker 配置（默认参数即为 esp_broker）
    await config_manager.preload_config()

    # 注册 esp_broker 配置变更监听器（默认参数即为 esp_broker）
    async def on_esp_broker_config_changed(new_config):
        logger.info(f"esp_broker config changed, new config: {new_config}")

    await config_manager.add_config_listener(callback=on_esp_broker_config_changed)
    # ==========================================

    # 初始化 Langfuse
    global langfuse, langfuse_handler
    try:
        langfuse = Langfuse()
        langfuse_handler = CallbackHandler()
        if langfuse.auth_check():
            logger.info("✅ Langfuse initialized successfully")
        else:
            logger.warning("⚠️ Langfuse authentication failed. Please check credentials.")
            langfuse = None
            langfuse_handler = None
    except Exception as e:
        logger.warning(f"⚠️ Langfuse not available: {e}. Tracing will be disabled.")
        langfuse = None
        langfuse_handler = None

    # 初始化MySQL连接池
    Database.init()
    # 初始化Redis连接池（可选）
    RedisClusterManager.init_cluster()

    # 加载中间件检查
    middle_health_check()
    
    # 初始化内存监控
    #monitor = init_memory_monitor("MyFastAPIApp")
    
    # 启动后台监控线程（每10秒采集一次）
    #monitor.start_background_monitoring(interval=10)
    
    # 定期检查内存泄漏（每5分钟）
    import threading
    
    # def periodic_leak_check():
    #     while True:
    #         try:
    #             report = monitor.analyze_leak()
    #             if report.get("leak_detected"):
    #                 # 这里可以发送警报，如邮件、Slack等
    #                 print(f"🚨 内存泄漏警报: {report}")
    #         except Exception as e:
    #             print(f"泄漏检查失败: {e}")
            
    #         # 每5分钟检查一次
    #         threading.Event().wait(300)
    
    # leak_check_thread = threading.Thread(
    #     target=periodic_leak_check,
    #     daemon=True,
    #     name="LeakCheckThread"
    # )
    # leak_check_thread.start()

    # app.state.executor = executor

    try:
        yield
    finally:
        logger.info("释放资源...")
        # 同步清理操作
        Database.close()
        RedisClusterManager.close()
        #monitor.stop_monitoring()


# FastAPI app initialization
app = FastAPI(
    title="Aira Assistant Agent API",
    description="门户对话智能体服务",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins="*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添加内存监控中间件
#app.add_middleware(MemoryMonitoringMiddleware)

# Register HITL API router
app.include_router(broker_hitl_router)


def prepare_initial_state(workflow_input: WorkflowInput) -> BaseState:
    """Prepare initial state from input."""
    workflow_run_id = str(uuid.uuid4())

    # Convert input to state format
    initial_state = {
        "sys_query": workflow_input.query,
        "sys_user_id": workflow_input.user,
        "sys_app_id": workflow_input.sys_app_id,
        "sys_workflow_id": workflow_input.sys_workflow_id,
        "sys_workflow_run_id": workflow_run_id,
        "sys_conversation_id": workflow_input.conversation_id,
        "sys_scene_type": workflow_input.scene_type,
        "sys_files": workflow_input.files,
        "input_variables": workflow_input.inputs,
        "sys_use_end_stream": workflow_input.inputs.get("use_end_stream", True),
    }

    return initial_state


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"message": "Aira Workflow API", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health_check():
    """Detailed health check."""
    
    monitor = get_memory_monitor()
    current = monitor.get_current_stats()
    
    try:
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "memory_mb": current['rss_mb'],
            "memory_percent": current['percent'],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Service unhealthy: {str(e)}")


# 中间件检查  当前有mysql 和 redis
@app.get("/middle_health")
def middle_health_check():
    from db.database import Database

    health_check_db = "failed"
    health_check_cache = "failed"

    try:
        if RedisClusterManager.is_enabled():
            health_check_cache = "successful"
        if Database.health_check():
            health_check_db = "successful"
        logger.info("The database loaded : " + health_check_db)
        logger.info("The cache loaded : " + health_check_cache)
        return {
            "status": "healthy",
            "middle_health": {
                "health_check_db": health_check_db,
                "health_check_cache": health_check_cache,
            },
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"middle unhealthy: {str(e)}",
        )


@app.post("/config/cache/clear")
async def clear_config_cache(cache_type: str, request: Request):
    """清除配置缓存接口"""
    config_manager = request.app.state.config_manager
    await config_manager.clear_cache(cache_type)
    return {
        "status": "success",
        "message": f"Config cache cleared, type: {cache_type}",
    }


@app.post("/v1/products/match")
def match_product(
    body: ProductMatchRequest,
    _: None = Depends(validate_product_match_token),
) -> Dict[str, Any]:
    """
    根据 input_product 匹配 workflow_test.aira_product_category_info 中最相似的一条 product_name。
    产品目录缓存 Redis 60 分钟；LLM 调用接入 Langfuse 追踪。
    """
    try:
        product_name = match_product_name(body.input_product)
    except Exception as e:
        logger.warning(
            "product_match_failed",
            error=str(e),
            input_product=body.input_product,
        )
        product_name = body.input_product

    return {"success": True, "message": "success", "data": product_name}


@app.post("/v1/workflows/run")
def execute_workflow(
    request: Request,
    workflow_input: WorkflowInput,
    workflow: BaseWorkflow = Depends(validate_token_and_get_wf),
):
    """
    Execute workflow synchronously , support streaming response and blocking response.

    Args:
        workflow_input: Workflow input parameters

    Returns:
        WorkflowOutput: Execution result
    """

    wf_type = workflow.workflow_type
    if wf_type != WF_TYPE_WORKFLOW:
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Workflow type must be {WF_TYPE_WORKFLOW}",
        )

    request_id = request.headers.get(WF_REQUEST_ID_HEADER_NAME)
    if request_id is None:
        request_id = str(uuid.uuid4())

    # use without langgraph execution environment
    request_id_ctx.set(request_id)

    # 从上游调用传递过来的trace_id, span_id
    upstream_trace_id = request.headers.get(UPSTREAM_TRACE_ID_HEADER_NAME)
    upstream_span_id = request.headers.get(UPSTREAM_SPAN_ID_HEADER_NAME)
    if upstream_trace_id and upstream_span_id:
        trace_info_ctx.set(
            {
                UPSTREAM_TRACE_ID_HEADER_NAME: upstream_trace_id,
                UPSTREAM_SPAN_ID_HEADER_NAME: upstream_span_id,
            }
        )

    workflow_run_id = str(uuid.uuid4())

    user_id = workflow_input.user

    # 构造脱敏后的日志参数
    # log_input = workflow_input.model_dump(exclude={"inputs": {"financial_data"}})
    # logger.info("workflow request start", request_param=log_input)
    logger.info("workflow request start", request_param=workflow_input)

    # workflow的模型输出模式的是非流式输出
    response_mode = workflow_input.response_mode or RESPONSE_MODE_BLOCKING
    # Prepare initial state
    initial_state = prepare_initial_state(workflow_input)
    initial_state["sys_workflow_run_id"] = workflow_run_id

    # use with langgraph execution environment through var_child_runnable_config
    initial_state["request_id"] = request_id
    # 将 trace_id 和 parent_span_id 存储到 node_span_ids["trace_context"] 中
    if "node_span_ids" not in initial_state:
        initial_state["node_span_ids"] = {}
    if "trace_context" not in initial_state["node_span_ids"]:
        initial_state["node_span_ids"]["trace_context"] = {}
    if upstream_trace_id:
        initial_state["node_span_ids"]["trace_context"]["trace_id"] = upstream_trace_id
    if upstream_span_id:
        initial_state["node_span_ids"]["trace_context"]["parent_span_id"] = upstream_span_id

    workflow_service = WorkflowGenerateService(workflow)
    if response_mode == RESPONSE_MODE_STREAMING:
        return StreamingResponse(
            workflow_service.generate(initial_state),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Workflow-Run-ID": workflow_run_id,
            },
        )
    elif response_mode == RESPONSE_MODE_BLOCKING:
        try:
            # Execute workflow
            result = workflow_service.execute(initial_state)
            return result

        except Exception as e:
            # logger.error(f"UnknownError_{e}", exc_info=True, user_id=user_id)
            status_code = _get_status_code_by_error_msg(str(e))
            raise HTTPException(
                status_code=status_code, detail=f"Workflow execution failed: {str(e)}"
            )
    else:
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unknown response mode: {response_mode}",
        )


@app.post("/v1/chat-messages/{task_id}/stop")
def stop_workflow(task_id: str):
    """
    Stop workflow execution.

    Args:
        workflow_run_id: ID of the workflow run to stop
    """
    stopped_cache_key = _generate_stopped_cache_key(task_id)

    ChatflowGenerateService.set_stoped(stopped_cache_key)
    return {"success": True, "message": "Workflow stopped successfully"}


def _generate_stopped_cache_key(workflow_run_id: str) -> str:
    """
    Generate stopped cache key
    :param workflow_run_id: workflow_run_id id
    :return:
    """
    return f"generate_task_stopped:{workflow_run_id}"


# import concurrent.futures
# executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)


@app.post("/v1/chat-messages")
async def stream_workflow(
    request: Request,
    workflow_input: WorkflowInput,
    workflow: BaseWorkflow = Depends(validate_token_and_get_wf),
):
    """
    Execute chatflow , support streaming response and blocking response.

    Args:
        workflow_input: Workflow input parameters
        workflow: workflow instance dependency injection
        request: Request

    Returns:
        StreamingResponse: Server-sent events stream
    """

    agent_id_prefix = workflow.__class__.__name__
    wf_type = workflow.workflow_type
    if wf_type != WF_TYPE_CHATFLOW:
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Workflow type must be {WF_TYPE_CHATFLOW}",
        )

    request_id = request.headers.get(WF_REQUEST_ID_HEADER_NAME)
    if request_id is None:
        request_id = str(uuid.uuid4())

    # use without langgraph execution environment
    request_id_ctx.set(request_id)

    # 从上游调用传递过来的trace_id, span_id
    upstream_trace_id = request.headers.get(UPSTREAM_TRACE_ID_HEADER_NAME)
    upstream_span_id = request.headers.get(UPSTREAM_SPAN_ID_HEADER_NAME)

    logger.info(
        "upstream_trace_info",
        upstream_trace_id=upstream_trace_id,
        upstream_span_id=upstream_span_id,
    )

    if upstream_trace_id and upstream_span_id:
        trace_info_ctx.set(
            {
                UPSTREAM_TRACE_ID_HEADER_NAME: upstream_trace_id,
                UPSTREAM_SPAN_ID_HEADER_NAME: upstream_span_id,
            }
        )

    workflow_run_id = str(uuid.uuid4())

    # 构造脱敏后的日志参数
    # log_input = workflow_input.model_dump(exclude={"inputs": {"financial_data"}})
    # logger.info("chatflow request start", request_param=log_input)
    logger.info("chatflow request start", request_param=workflow_input)

    response_mode = workflow_input.response_mode or RESPONSE_MODE_STREAMING
    # Prepare initial state
    initial_state = prepare_initial_state(workflow_input)
    initial_state["sys_workflow_run_id"] = workflow_run_id
    
    agent_id = workflow_input.agent_id or "0000"
    initial_state["sys_agent_id"] = f"{agent_id_prefix}_{agent_id}"

    # use with langgraph execution environment through var_child_runnable_config
    initial_state["request_id"] = request_id
    # 将 trace_id 和 parent_span_id 存储到 node_span_ids["trace_context"] 中
    if "node_span_ids" not in initial_state:
        initial_state["node_span_ids"] = {}
    if "trace_context" not in initial_state["node_span_ids"]:
        initial_state["node_span_ids"]["trace_context"] = {}
    if upstream_trace_id:
        initial_state["node_span_ids"]["trace_context"]["trace_id"] = upstream_trace_id
    if upstream_span_id:
        initial_state["node_span_ids"]["trace_context"]["parent_span_id"] = upstream_span_id

    chat_service = ChatflowGenerateService(workflow)
    
    from workflow.services.broker_hitl_service import BrokerHitlService
    
    chat_service.bind_interrupt_hook(BrokerHitlService(None))
    
    negotiation_config = {}
    try:
        # 强制从远程获取最新配置，确保实时性
        config = await request.app.state.config_manager.get_config(force_refresh=True)
        negotiation_config = config.get("negotiation", {}) or {}
    except Exception as e:
        logger.warning(f"failed to load negotiation config: {e}")
        # 失败时使用缓存的配置（不强制刷新）
        try:
            config = await request.app.state.config_manager.get_config()
            negotiation_config = config.get("negotiation", {}) or {}
        except Exception as e2:
            logger.warning(f"failed to load negotiation config from cache: {e2}")
    
    if "negotiation_config" not in initial_state:
        initial_state["negotiation_config"] = negotiation_config
    else:
        initial_state["negotiation_config"] = negotiation_config or initial_state.get("negotiation_config", {})
    
    if response_mode == RESPONSE_MODE_STREAMING:
        # future = executor.submit(stream_service.generate, initial_state)
        return StreamingResponse(
            chat_service.generate(initial_state),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Workflow-Run-ID": workflow_run_id,
            },
        )
    elif response_mode == RESPONSE_MODE_BLOCKING:
        try:
            # Execute workflow
            result = chat_service.execute(initial_state)
            return result

        except Exception as e:
            # logger.error(f"UnknownError_{e}", exc_info=True, user_id=user_id)
            status_code = _get_status_code_by_error_msg(str(e))
            raise HTTPException(
                status_code=status_code, detail=f"Workflow execution failed: {str(e)}"
            )
    else:
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unknown response mode: {response_mode}",
        )


# ugly fix
def _get_status_code_by_error_msg(error_msg: str) -> int:
    """Get status code by error message."""
    if "status_code: 4" in error_msg:
        return HTTP_403_FORBIDDEN
    else:
        return HTTP_500_INTERNAL_SERVER_ERROR


def format_stream_chunk(
    chunk_id: int, chunk_type: str, data: Dict[str, Any], workflow_run_id: str
) -> str:
    """Format a streaming chunk as SSE format."""
    chunk = StreamChunk(
        chunk_id=str(chunk_id),
        type=chunk_type,
        data=data,
        timestamp=datetime.utcnow().isoformat(),
    )

    # Format as Server-Sent Events
    return f"data: {chunk.model_dump_json()}\n\n"




# Exception handlers
@app.exception_handler(WorkflowError)
def workflow_error_handler(request, exc: WorkflowError):
    """Handle workflow-specific errors."""
    return {"error": "WorkflowError", "message": str(exc), "status_code": 400}


@app.exception_handler(StateValidationError)
def state_validation_error_handler(request, exc: StateValidationError):
    """Handle state validation errors."""
    return {"error": "StateValidationError", "message": str(exc), "status_code": 400}


def _extract_json_from_markdown(content: str) -> str:
    """
    从 markdown 代码块中提取 JSON 内容
    支持格式: ```json\n...\n``` 或 ```\n...\n``` 或纯 JSON
    """
    content = content.strip()

    # 检查是否有 markdown 代码块标记
    if content.startswith("```"):
        # 移除开头的 ```json 或 ```
        lines = content.split("\n")
        # 移除第一行的代码块标记
        if lines[0].startswith("```"):
            lines = lines[1:]
        # 移除最后一行的代码块标记
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    return content


@app.get("/memory-intensive")
async def memory_intensive():
    """
    内存密集型操作，用于测试监控
    """
    monitor = get_memory_monitor()
    
    # 记录操作开始
    monitor.snapshot("memory_intensive_start")
    
    # 创建大量对象
    data = []
    for i in range(100000):
        data.append(f"string_{i}" * 10)
    
    # 记录操作中间状态
    monitor.snapshot("memory_intensive_middle")
    
    # 清理部分数据
    del data[50000:]
    
    # 记录操作结束
    monitor.snapshot("memory_intensive_end")
    
    return {
        "message": "内存密集型操作完成",
        "remaining_items": len(data)
    }


if __name__ == "__main__":
    # Run the server
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
