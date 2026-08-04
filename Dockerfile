# 基础镜像
FROM wlyd-acr-registry.cn-zhangjiakou.cr.aliyuncs.com/wanmol-dev/wf-base:v3.0

# 设置工作目录
WORKDIR /app

# 复制依赖文件
#COPY requirements.txt ./

# 安装依赖
# RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
RUN pip install PyPDF2 python-docx openpyxl  -i https://mirrors.aliyun.com/pypi/simple/
#RUN pip install markitdown  -i https://mirrors.aliyun.com/pypi/simple/
RUN pip install xlrd -i https://mirrors.aliyun.com/pypi/simple/
#RUN pip install python-pptx  -i https://mirrors.aliyun.com/pypi/simple/
RUN pip install langgraph-checkpoint-mysql[pymysql]  -i https://mirrors.aliyun.com/pypi/simple/
RUN pip install --progress-bar off grpcio grpcio-tools protobuf -i https://mirrors.aliyun.com/pypi/simple/

#RUN pip install PyPDF2 python-docx openpyxl xlrd  -i https://mirrors.aliyun.com/pypi/simple/
#RUN pip install pdftotext markitdown python-pptx  -i https://mirrors.aliyun.com/pypi/simple/
#RUN pip install langgraph-checkpoint-mysql[pymysql]  -i https://mirrors.aliyun.com/pypi/simple/
#RUN pip install --progress-bar off grpcio grpcio-tools protobuf -i https://mirrors.aliyun.com/pypi/simple/
# 复制项目全部代码
COPY . .

RUN ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && echo "Asia/Shanghai" > /etc/timezone

# 设置环境变量（如有 .env 可取消注释）
#COPY .env .env

ARG ENV
ARG WORKFLOW_SCENE_TYPE

ENV APP_ENV=${ENV}
ENV WORKFLOW_SCENE_TYPE=${WORKFLOW_SCENE_TYPE}

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000","--workers","2"]
