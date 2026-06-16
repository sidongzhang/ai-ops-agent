#!/usr/bin/env python3
"""
Producer: 每10秒向 Kafka 发送一条传感器数据
"""
import time
import json
import random
import logging
import os
import sys

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [PRODUCER] %(levelname)s %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'localhost:9092')
TOPIC = 'sensor-data'
INTERVAL = int(os.getenv('PRODUCER_INTERVAL', '10'))


def create_producer(retries=10):
    for i in range(retries):
        try:
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_BROKER,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                request_timeout_ms=5000,
            )
            logger.info(f"成功连接 Kafka: {KAFKA_BROKER}")
            return producer
        except NoBrokersAvailable:
            logger.warning(f"Kafka 未就绪，{i+1}/{retries} 次重试（5秒后）...")
            time.sleep(5)
    logger.error("无法连接 Kafka，退出")
    sys.exit(1)


def main():
    logger.info("Producer 启动中...")
    producer = create_producer()

    count = 0
    sensors = ['sensor_A', 'sensor_B', 'sensor_C', 'sensor_D', 'sensor_E']

    logger.info(f"开始发送数据，间隔 {INTERVAL} 秒")
    while True:
        count += 1
        data = {
            'id': count,
            'sensor': random.choice(sensors),
            'value': round(random.uniform(10.0, 99.9), 2),
            'unit': 'celsius',
            'timestamp': time.time(),
        }
        future = producer.send(TOPIC, data)
        future.get(timeout=10)
        logger.info(f"已发送第 {count} 条: sensor={data['sensor']} value={data['value']}")
        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()
