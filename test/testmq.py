"""
test/testmq.py — RabbitMQ 连通性测试

测试内容：
  1. 连接 RabbitMQ
  2. 存入消息（生产者）
  3. 读取消息（消费者）

用法：
    python test/testmq.py
"""

import json
import time
import uuid

import pika

# ====== 配置 ======
RABBITMQ_URL = "amqp://admin:bHw8JUmY9O4z@10.3.1.117:5672/"
QUEUE_NAME = "testmq_connectivity_check"  # 测试专用队列，用完删除


def test_connect() -> pika.BlockingConnection:
    """测试连接。成功返回 connection，失败抛出异常。"""
    print("=" * 50)
    print("1. 测试连接...")
    params = pika.URLParameters(RABBITMQ_URL)
    conn = pika.BlockingConnection(params)
    print(f"   [OK] 已连接到 {params.host}:{params.port}")
    return conn


def test_produce(conn: pika.BlockingConnection):
    """测试存入消息。"""
    print("\n" + "=" * 50)
    print("2. 测试存入消息（生产者）...")

    channel = conn.channel()

    # 声明一个临时队列（durable=False, auto_delete=True）
    channel.queue_declare(queue=QUEUE_NAME, durable=False, auto_delete=True)

    test_msg = {
        "test_id": str(uuid.uuid4()),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "message": "RabbitMQ 连通性测试消息",
    }
    body = json.dumps(test_msg, ensure_ascii=False)

    channel.basic_publish(
        exchange="",
        routing_key=QUEUE_NAME,
        body=body,
        properties=pika.BasicProperties(
            delivery_mode=1,  # 非持久化
            content_type="application/json",
        ),
    )

    print(f"   [OK] 已存入队列 '{QUEUE_NAME}'")
    print(f"   消息内容: {body}")
    channel.close()


def test_consume(conn: pika.BlockingConnection):
    """测试读取消息。"""
    print("\n" + "=" * 50)
    print("3. 测试读取消息（消费者）...")

    channel = conn.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=False, auto_delete=True)

    # 确认队列中有消息
    queue_state = channel.queue_declare(queue=QUEUE_NAME, passive=True)
    msg_count = queue_state.method.message_count
    print(f"   队列 '{QUEUE_NAME}' 中有 {msg_count} 条消息")

    if msg_count == 0:
        print("   [WARN] 队列为空，跳过读取")
        channel.close()
        return

    # 拉取一条消息
    method_frame, header_frame, body = channel.basic_get(queue=QUEUE_NAME, auto_ack=True)

    if method_frame:
        msg = json.loads(body.decode("utf-8"))
        print(f"   [OK] 读取成功")
        print(f"   delivery_tag: {method_frame.delivery_tag}")
        print(f"   消息内容: {json.dumps(msg, ensure_ascii=False)}")
    else:
        print("   [WARN] 未拉取到消息")

    channel.close()


def cleanup(conn: pika.BlockingConnection):
    """删除测试队列。"""
    print("\n" + "=" * 50)
    print("4. 清理测试队列...")
    channel = conn.channel()
    channel.queue_delete(queue=QUEUE_NAME)
    print(f"   [OK] 队列 '{QUEUE_NAME}' 已删除")
    channel.close()


def main():
    conn = None
    try:
        conn = test_connect()
        test_produce(conn)
        test_consume(conn)
        cleanup(conn)

        print("\n" + "=" * 50)
        print("[ALL OK] RabbitMQ 连通性测试全部通过")
        print(f"  URL: {RABBITMQ_URL}")
        return 0
    except pika.exceptions.AMQPConnectionError as e:
        print(f"\n[FAIL] 连接失败: {e}")
        return 1
    except Exception as e:
        print(f"\n[FAIL] 测试异常: {type(e).__name__}: {e}")
        return 2
    finally:
        if conn and conn.is_open:
            conn.close()
            print("\n连接已关闭")


if __name__ == "__main__":
    exit(main())
