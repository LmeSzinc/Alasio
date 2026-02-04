def worker_loop(conn, func):
    """
    子进程运行循环。
    当 Pipe 另一端(主进程)关闭或断开时，自动退出。

    worker_loop 是一个单独的 python 文件，来避免 worker 进程启动的时候导入 ProcessPool
    """
    try:
        while True:
            try:
                args, kwargs = conn.recv()
            except (OSError, EOFError, BrokenPipeError):
                # pipe broken, exit process
                break

            try:
                result = func(*args, **kwargs)
            except Exception as e:
                # failed, send ERR
                try:
                    conn.send(('ERR', e))
                    continue
                except (OSError, EOFError, BrokenPipeError):
                    # pipe broken, exit process
                    break

            # success, send OK
            try:
                conn.send(('OK', result))
            except (OSError, EOFError, BrokenPipeError):
                # pipe broken, exit process
                break
    except KeyboardInterrupt:
        # suppress KeyboardInterrupt of worker process logging on terminal
        pass
