def worker_loop(conn, func):
    """
    子进程运行循环。
    当 Pipe 另一端(主进程)关闭或断开时，自动退出。

    worker_loop 是一个单独的 python 文件，来避免 worker 进程启动的时候导入 ProcessPool
    """
    while True:
        try:
            # 阻塞等待任务
            args, kwargs = conn.recv()
        except (EOFError, KeyboardInterrupt, OSError):
            # 连接断开，退出进程
            break

        try:
            result = func(*args, **kwargs)
            conn.send(('OK', result))
        except Exception as e:
            # 捕获业务逻辑异常发回主进程
            try:
                conn.send(('ERR', e))
            except (OSError, BrokenPipeError):
                break
