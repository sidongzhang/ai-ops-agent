#!/usr/bin/env python3
"""
Consumer: 从 Kafka 消费数据，写入 MySQL
"""
import time
import json
import logging
import os
import sys

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

import mysql.connector
from mysql.connector import Error as MySQLError
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CONSUMER] %(levelname)s %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'localhost:9092')
TOPIC = 'sensor-data'
MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'user': os.getenv('MYSQL_USER', 'opsuser'),
    'password': os.getenv('MYSQL_PASSWORD', 'opspass'),
    'database': os.getenv('MYSQL_DB', 'opsdb'),
}


def wait_for_mysql(retries=30):
    for i in range(retries):
        try:
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sensor_data (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    sensor_id VARCHAR(50) NOT NULL,
                    value FLOAT NOT NULL,
                    unit VARCHAR(20) DEFAULT 'celsius',
                    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            cursor.close()
            conn.close()
            logger.info("MySQL 连接成功，表已就绪")
            return
        except MySQLError as e:
            logger.warning(f"MySQL 未就绪 {i+1}/{retries}: {e}")
            time.sleep(3)
    logger.error("无法连接 MySQL，退出")
    sys.exit(1)


def save_to_db(data: dict):
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sensor_data (sensor_id, value, unit) VALUES (%s, %s, %s)",
        (data.get('sensor', 'unknown'), data.get('value', 0), data.get('unit', ''))
    )
    conn.commit()
    cursor.close()
    conn.close()


def create_consumer(retries=10):
    for i in range(retries):
        try:
            consumer = KafkaConsumer(
                TOPIC,
                bootstrap_servers=KAFKA_BROKER,
                value_deserializer=lambda v: v,  # 原始 bytes，解析在循环内做
                group_id='ops-consumer-group',
                auto_offset_reset='earliest',
                consumer_timeout_ms=1000,
            )
            logger.info(f"成功连接 Kafka: {KAFKA_BROKER}")
            return consumer
        except NoBrokersAvailable:
            logger.warning(f"Kafka 未就绪，{i+1}/{retries} 次重试（5秒后）...")
            time.sleep(5)
    logger.error("无法连接 Kafka，退出")
    sys.exit(1)


def main():
    logger.info("Consumer 启动中...")
    wait_for_mysql()
    consumer = create_consumer()

    logger.info("开始消费消息...")
    saved = 0
    skipped = 0
    while True:
        for message in consumer:
            # 解析 JSON，失败则跳过这条消息，不影响后续消费
            try:
                data = json.loads(message.value.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                skipped += 1
                logger.warning(f'跳过无法解析的消息 (共跳过 {skipped} 条): {e} | raw={message.value[:80]}')
                continue

            # 字段校验，缺少必要字段的消息同样跳过
            if not isinstance(data, dict) or 'sensor' not in data or 'value' not in data:
                skipped += 1
                logger.warning(f'跳过格式不完整的消息 (共跳过 {skipped} 条): {data}')
                continue

            try:
                save_to_db(data)
                saved += 1
                logger.info(f"已保存第 {saved} 条: {data.get('sensor')} = {data.get('value')}")
            except MySQLError as e:
                logger.error(f"写入 MySQL 失败: {e}")


if __name__ == '__main__':
    main()
