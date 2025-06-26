from typing import Optional

import httpx
import msgspec
import trio


class LatencyInfo(msgspec.Struct):
    """
    Stores the result of a single latency probe.
    - state: "In Window", "Fastest Fallback", "Timeout", "Error", "Cancelled".
    - url: The probed URL.
    - latency: Latency in milliseconds for successful probes, otherwise None.
    - status_code: The HTTP status code for successful probes, otherwise None.
    - error: An error message for failed probes, otherwise None.
    """
    state: str
    url: str
    latency: Optional[float] = None
    status_code: Optional[int] = None
    error: str = ''


# Define sort order priority
STATE_PRIORITY = {
    'In Window': 0,
    'Fastest Fallback': 1,
    'Cancelled': 2,
    'Error': 3,
    'Timeout': 4,
}


class LatencyTest:
    """
    A utility class to run concurrent latency tests against a list of URLs
    with a two-phase timing logic (window + fallback) and a hard timeout.
    """

    def __init__(self, urls):
        """
        Initializes the latency tester.

        Args:
            urls (list[str], str): A list of URL strings to be tested.
        """
        if isinstance(urls, str):
            urls = [urls]
        if not isinstance(urls, list):
            raise TypeError('urls must be a list of strings.')
        self.urls = urls
        self.result: "list[LatencyInfo]" = []

    def measure(self, window=1.0, timeout=5.0):
        """
        Executes the concurrent latency test.

        This method is synchronous and blocks until the test is complete.
        The test follows a three-phase logic:
        1. Wait for `window` seconds and collect all successful responses.
        2. If none, wait for the single fastest response (the "fallback").
        3. The entire process is cancelled if it takes longer than `timeout` seconds.

        Results are sorted and stored in the `self.result` attribute.

        Args:
            window (float): The time in seconds to wait for initial results.
            timeout (float): The hard timeout in seconds for the entire test process.

        Returns:
            list[LatencyInfo]: A sorted list of LatencyInfo objects.
        """
        if window >= timeout:
            raise ValueError(f'The window={window} duration must be less than the timeout={timeout}.')

        trio.run(self._async_measure, window, timeout)

        # Sort results immediately after the test concludes
        self._sort_results()

        return self.result

    async def _async_measure(self, window: float, timeout: float):
        """
        The internal async implementation of the measurement logic.

        Args:
            window (float):
            timeout (float):
        """
        self.result.clear()

        # Use a dictionary to track the final state of each URL
        final_infos: "dict[str, LatencyInfo]" = {url: None for url in self.urls}

        try:
            with trio.fail_after(timeout):
                async with httpx.AsyncClient(follow_redirects=True) as client:
                    send_channel, receive_channel = trio.open_memory_channel(len(self.urls))

                    async with trio.open_nursery() as nursery:
                        # Start all probe tasks
                        for url in self.urls:
                            nursery.start_soon(
                                self._probe_url, client, url, send_channel.clone(), timeout
                            )

                        # --- Phase 1: The Window ---
                        in_window_results = []
                        with trio.move_on_after(window):
                            async for info in receive_channel:
                                in_window_results.append(info)

                        # --- Phase 2: Decision and Fallback ---
                        if in_window_results:
                            # We got results within the window
                            for info in in_window_results:
                                info.state = 'In Window'
                                final_infos[info.url] = info
                        else:
                            # No results yet, wait for the first one as fallback
                            try:
                                info = await receive_channel.receive()
                                info.state = 'Fastest Fallback'
                                final_infos[info.url] = info
                            except trio.EndOfChannel:
                                # All probes failed before one could return
                                pass

                        # Now that we have our primary result(s), cancel the nursery.
                        # This will stop all other pending probes.
                        nursery.cancel_scope.cancel()

        except trio.TooSlowError:
            # This is triggered by `fail_after(timeout)`
            # Mark all non-finished tasks as "Timeout"
            for url, info in final_infos.items():
                if info is None:
                    info = LatencyInfo(state='Timeout', url=url, error=f'Process timed out after {timeout}s')
                    final_infos[url] = info

        # Collect final statuses of all probes (including cancelled/errored ones)
        # This part is a bit complex but ensures every URL has a result.
        remaining_urls = [url for url, info in final_infos.items() if info is None]
        for url in remaining_urls:
            # Probes that were cancelled by the nursery exit will be marked as such
            info = LatencyInfo(state='Cancelled', url=url, error='Cancelled after primary result was found')
            final_infos[url] = info

        self.result = list(final_infos.values())

    @staticmethod
    async def _probe_url(client, url, send_channel, timeout=5.0):
        """
        Probes a single URL and sends a preliminary LatencyInfo object.

        Args:
            client (httpx.AsyncClient):
            url (str):
            send_channel (trio.MemorySendChannel):
            timeout (float):
        """
        info = None
        try:
            start_time = trio.current_time()
            # The timeout for individual requests should be generous,
            # as the overall logic is controlled by window/timeout.
            timeout += 5
            response = await client.head(url, timeout=timeout)
            response.raise_for_status()
            duration_ms = (trio.current_time() - start_time) * 1000
            info = LatencyInfo(state='OK', url=url, latency=duration_ms, status_code=response.status_code)
        except httpx.TimeoutException:
            info = LatencyInfo(state='Timeout', url=url, error=f'Request timed out after {timeout}s')
        except httpx.RequestError as e:
            info = LatencyInfo(state='Error', url=url, error=f'{type(e).__name__}: {e}')
        except trio.Cancelled:
            # This is the expected outcome for slow tasks, so we don't create a final info object here.
            # The main logic will mark it as "Cancelled".
            pass
        except Exception as e:
            info = LatencyInfo(state='Error', url=url, error=f'{type(e).__name__}: {e}')

        # Send result only if it wasn't cancelled
        if info:
            async with send_channel:
                await send_channel.send(info)

    def _sort_results(self):
        """
        Sorts the final results list in-place.
        """

        def sort_key(info: LatencyInfo):
            # Sort by state priority, then by latency (if applicable)
            return STATE_PRIORITY.get(info.state, 99), info.latency or float('inf')

        self.result.sort(key=sort_key)

    def print_results(self):
        """
        Formats and prints the test results in a sorted table.
        """
        if not self.result:
            print("No test results available. Please run measure() first.")
            return

        print("\n" + "=" * 95)
        print(f"{' ' * 40}Latency Test Results")
        print("=" * 95)
        print(f"{'State':<20} {'Latency (ms)':<15} {'HTTP Code':<12} {'URL':<40} {'Details'}")
        print(f"{'-' * 18:<20} {'-' * 13:<15} {'-' * 10:<12} {'-' * 38:<40} {'-' * 20}")

        for info in self.result:
            latency_str = f"{info.latency:.2f}" if info.latency is not None else "N/A"
            code_str = str(info.status_code) if info.status_code is not None else "N/A"
            url_str = (info.url[:37] + '...') if len(info.url) > 40 else info.url

            print(f"{info.state:<20} {latency_str:<15} {code_str:<12} {url_str:<40} {info.error}")
        print("-" * 95)


# Usage example
if __name__ == "__main__":
    test_urls = [
        'https://pypi.tuna.tsinghua.edu.cn/simple/',  # Fast, should be "In Window"
        'https://mirrors.aliyun.com/pypi/simple/',  # Fast, should be "In Window"
        'https://httpbin.org/delay/3',  # Slow, should be "Cancelled" in the first test
        'https://pypi.org/simple/',  # Medium, might be "In Window"
        'https://this-site-definitely-does-not-exist.com',  # Should be "Error"
        'https://httpbin.org/status/404',  # Should be "Error"
        'https://google.com:81'  # Individual request timeout -> "Error"
    ]

    tester = LatencyTest(urls=test_urls)
    tester.measure(window=2.0, timeout=5.0)
    tester.print_results()
    # You can also access results in `tester.result`

    # Example output to display
    """
    ===============================================================================================
                                            Latency Test Results
    ===============================================================================================
    State                Latency (ms)    HTTP Code    URL                                      Details
    ------------------   -------------   ----------   --------------------------------------   --------------------
    In Window            201.12          200          https://mirrors.aliyun.com/pypi/simple/  
    In Window            335.01          200          https://pypi.tuna.tsinghua.edu.cn/sim... 
    In Window            641.90          200          https://pypi.org/simple/                 
    In Window            N/A             N/A          https://this-site-definitely-does-not... ConnectError: [Errno 
    11001] getaddrinfo failed
    Cancelled            N/A             N/A          https://httpbin.org/delay/3              Cancelled after 
    primary result was found
    Cancelled            N/A             N/A          https://httpbin.org/status/404           Cancelled after 
    primary result was found
    Cancelled            N/A             N/A          https://google.com:81                    Cancelled after 
    primary result was found
    -----------------------------------------------------------------------------------------------
    """
