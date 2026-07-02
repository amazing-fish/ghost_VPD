from app.tasks.huey_base import huey
from gevent import monkey;monkey.patch_all()
from app.constant import MAX_HUEY_WORKS
from app.tasks.vpd_tasks import start_huey_consumer
import multiprocessing
from time import sleep
from app import create_app
app = create_app()


if __name__ == '__main__':
    multiprocessing.freeze_support()

    periodic = True

    # 清空队列/存储结果
    huey.flush()
    huey.storage.flush_results()

    for _ in range(MAX_HUEY_WORKS):
        p = multiprocessing.Process(target=start_huey_consumer, kwargs={'periodic': periodic})
        p.daemon = True
        p.start()

        sleep(0.5)  # 分批启动进程

        if periodic:  # 仅启动一个调度器
            periodic = False

    app.run(host='0.0.0.0', port=5000)
