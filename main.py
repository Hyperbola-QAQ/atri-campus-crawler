import asyncio
import json
import os
from typing import Dict, Any
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from dotenv import load_dotenv
from utils.log import logger
from adapter.hnucm_adapter import HNUCMAdapter
from utils.nacos import get_nacos_config_client
from v2.nacos import ConfigParam

# 加载默认
load_dotenv()
load_dotenv(f'.env.{os.getenv("ENVIRONMENT", "dev")}')

# 爬虫配置
SCHOOL_ADAPTERS = {
    "HNUCM": HNUCMAdapter
}


async def get_kafka_config():
    """从Nacos获取Kafka配置"""
    config_client = await get_nacos_config_client()
    
    if config_client is None:
        logger.warning("获取nacos配置客户端失败, 使用默认配置")
        return {
            "bootstrap_servers": "localhost:9092",
            "input_topic": "atri-crawler-input",
            "output_topic": "atri-crawler-output",
            "group_id": "atri-crawler-group"
        }

    # 从nacos获取kafka配置
    config_param = ConfigParam(
        data_id="dev.kafka.json",
        group="KAFKA",
    )

    try:
        # 获取配置
        config_str: str = await config_client.get_config(config_param)
        if config_str:
            # 解析配置
            config: dict = json.loads(config_str)
            return config
        raise ValueError("Kafka获取到异常配置")
    except json.JSONDecodeError as e:
        logger.error(f"Kafka配置JSON解析失败: {e}")
        return {
            "bootstrap_servers": "localhost:9092",
            "input_topic": "atri-crawler-input",
            "output_topic": "atri-crawler-output",
            "group_id": "atri-crawler-group"
        }
    except Exception as e:
        logger.warning(f"获取Kafka配置失败: {e}, 使用默认配置")
        return {
            "bootstrap_servers": "localhost:9092",
            "input_topic": "atri-crawler-input",
            "output_topic": "atri-crawler-output",
            "group_id": "atri-crawler-group"
        }


async def process_crawl_request(request_data: Dict[str, Any], producer: AIOKafkaProducer, kafka_config: dict) -> None:
    """处理爬虫请求"""
    request_id = request_data.get('request_id')
    school = request_data.get('school')
    action = request_data.get('action')
    username: str = request_data.get('username') or 'WrongUsername'
    password: str = request_data.get('password') or 'WrongPassword'
    params = request_data.get('params', {})
    
    logger.info(f"接收到爬虫请求: request_id={request_id}, school={school}, action={action}")
    
    response_data = {
        'request_id': request_id,
        'status': 'failed',
        'data': None,
        'error': None
    }
    
    try:
        # 检查学校适配器
        if school not in SCHOOL_ADAPTERS:
            raise ValueError(f"不支持的学校: {school}")
        
        # 创建适配器实例
        adapter = SCHOOL_ADAPTERS[school]()
        
        # 执行登录获取cookies
        login_success, message, cookies = await adapter.login(username, password)
        if not login_success:
            raise ValueError(f"登录失败: {message}")
        
        # 根据不同的action执行相应的爬虫操作
        if action == 'login':
            response_data['data'] = {'success': True, 'message': '登录成功'}
            response_data['status'] = 'success'
            
        elif action == 'get_profile':
            # 获取个人信息
            success, message, profile_data = await adapter.get_profile(cookies, username)
            if success and profile_data:
                # 转换Profile对象为字典
                if hasattr(profile_data, 'dict'):
                    response_data['data'] = profile_data.model_dump_json()
                else:
                    response_data['data'] = profile_data
                response_data['status'] = 'success'
            else:
                raise ValueError(f"获取个人信息失败: {message}")
            
        elif action == 'get_grades':
            # 获取成绩信息
            semester = params.get('semester', '')
            success, message, grades_data = await adapter.get_grades(cookies, username, semester)
            if success and grades_data:
                # 转换GradeItem列表为字典列表
                response_data['data'] = [
                    grade.model_dump_json() if hasattr(grade, 'dict') else grade 
                    for grade in grades_data
                ]
                response_data['status'] = 'success'
            else:
                raise ValueError(f"获取成绩失败: {message}")
            
        elif action == 'get_course_schedule':
            # 获取课程表
            semester = params.get('semester', '')
            success, message, course_data = await adapter.get_course_schedule(cookies, username, semester)
            if success and course_data:
                # 转换CourseScheduleItem列表为字典列表
                response_data['data'] = [
                    course.model_dump_json() if hasattr(course, 'dict') else course 
                    for course in course_data
                ]
                response_data['status'] = 'success'
            else:
                raise ValueError(f"获取课程表失败: {message}")
            
        else:
            raise ValueError(f"不支持的操作: {action}")
            
    except Exception as e:
        logger.error(f"处理请求失败: {str(e)}", exc_info=True)
        response_data['error'] = str(e)
    
    # 发送响应到Kafka
    await producer.send_and_wait(
        kafka_config["output_topic"],
        response_data,
        key=request_id.encode('utf-8') if request_id else None
    )
    logger.info(f"响应已发送到Kafka: request_id={request_id}, status={response_data['status']}")

async def kafka_consumer_loop() -> None:
    """Kafka消费者主循环"""
    # 获取Kafka配置
    kafka_config = await get_kafka_config()
    
    if not kafka_config:
        logger.error("Kafka配置获取失败")
        return
    
    # 创建aiokafka消费者和生产者
    def safe_json_deserializer(m):
        if not m:
            return {}
        try:
            return json.loads(m.decode('utf-8'))
        except json.JSONDecodeError as e:
            logger.error(f"JSON反序列化失败: {e}, 原始数据: {m}")
            return {}
    
    consumer = AIOKafkaConsumer(
        kafka_config["input_topic"],
        bootstrap_servers=kafka_config["bootstrap_servers"],
        group_id=kafka_config["group_id"],
        auto_offset_reset="latest",
        value_deserializer=safe_json_deserializer,
        key_deserializer=lambda m: m.decode('utf-8') if m else None
    )
    
    # 修改生产者的key_serializer，使其能正确处理bytes和string类型
    def safe_key_serializer(v):
        if v is None:
            return None
        if isinstance(v, bytes):
            return v
        return v.encode('utf-8')
    
    # 修改生产者的value_serializer，使其能正确处理包含bytes的对象
    def safe_value_serializer(v):
        def default_serializer(obj):
            if isinstance(obj, bytes):
                return obj.decode('utf-8', errors='ignore')
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
        
        return json.dumps(v, default=default_serializer).encode('utf-8')
    
    producer = AIOKafkaProducer(
        bootstrap_servers=kafka_config["bootstrap_servers"],
        value_serializer=safe_value_serializer,
        key_serializer=safe_key_serializer
    )
    
    # 启动消费者和生产者
    await consumer.start()
    await producer.start()
    
    logger.info(f"Kafka消费者已启动，监听主题: {kafka_config['input_topic']}")
    
    try:
        async for msg in consumer:
            try:
                # 增加对消息体是否为空的判断
                if msg.value is None:
                    logger.warning("接收到空消息，跳过处理")
                    continue
                    
                request_data = msg.value
                # 添加类型和空值检查
                if not isinstance(request_data, dict):
                    logger.warning(f"接收到无效消息格式，跳过处理: {request_data}")
                    continue
                
                logger.debug(f"接收到消息: {request_data}")
                
                # 异步处理请求
                asyncio.create_task(process_crawl_request(request_data, producer, kafka_config))
                
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析错误: {str(e)}, 消息内容: {msg.value if msg.value else 'None'}")
            except Exception as e:
                logger.error(f"处理消息时出错: {str(e)}", exc_info=True)
                
    except KeyboardInterrupt:
        logger.info("接收到中断信号，正在停止...")
    except Exception as e:
        logger.error(f"Kafka消费过程中出现异常: {str(e)}", exc_info=True)
    finally:
        await consumer.stop()
        await producer.stop()
        logger.info("Kafka连接已关闭")


def main() -> None:
    """主函数"""
    logger.info("ATRI爬虫服务启动中...")
    
    try:
        # 启动异步事件循环
        asyncio.run(kafka_consumer_loop())
    except KeyboardInterrupt:
        logger.info("服务已手动停止")
    except Exception as e:
        logger.error(f"服务异常停止: {str(e)}", exc_info=True)


if __name__ == "__main__":
    main()
