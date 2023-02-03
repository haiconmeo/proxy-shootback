import asyncio
import re
from asyncio.streams import StreamReader, StreamWriter
from contextlib import closing
from typing import Tuple, Optional
import socket
import async_timeout

StreamPair = Tuple[StreamReader, StreamWriter]


class RawHTTPParser:
    pattern = re.compile(
        br'(?P<method>[a-zA-Z]+) (?P<uri>(\w+://)?(?P<host>[^\s\'\"<>\[\]{}|/:]+)(:(?P<port>\d+))?[^\s\'\"<>\[\]{}|]*) ')
    uri: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    method: Optional[str] = None
    is_parse_error: bool = False

    def __init__(self, raw: bytes):
        rex = self.pattern.match(raw)
        if rex:
            to_int = RawHTTPParser.to_int
            to_str = RawHTTPParser.to_str

            self.uri = to_str(rex.group('uri'))
            self.host = to_str(rex.group('host'))
            self.method = to_str(rex.group('method'))
            self.port = to_int(rex.group('port'))
        else:
            self.is_parse_error = True

    @staticmethod
    def to_str(item: Optional[bytes]) -> Optional[str]:
        if item:
            return item.decode('charmap')

    @staticmethod
    def to_int(item: Optional[bytes]) -> Optional[int]:
        if item:
            return int(item)

    def __str__(self):
        return str(dict(URI=self.uri, HOST=self.host, PORT=self.port, METHOD=self.method))


async def forward_stream(reader: StreamReader, writer: StreamWriter, event: asyncio.Event):
    while not event.is_set():
        try:
            data = await asyncio.wait_for(reader.read(1024), 1)
        except asyncio.TimeoutError:
            continue

        if data == b'':  # when it closed
            event.set()
            break

        writer.write(data)
        await writer.drain()


async def relay_stream(local_stream: StreamPair, remote_stream: StreamPair):
    local_reader, local_writer = local_stream
    remote_reader, remote_writer = remote_stream

    close_event = asyncio.Event()

    await asyncio.gather(
        forward_stream(local_reader, remote_writer, close_event),
        forward_stream(remote_reader, local_writer, close_event)
    )


async def https_handler(reader: StreamReader, writer: StreamWriter, request: RawHTTPParser):
    remote_reader, remote_writer = await asyncio.open_connection(request.host, request.port)
    with closing(remote_writer):
        writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        await writer.drain()
        print('HTTPS connection established')

        await relay_stream((reader, writer), (remote_reader, remote_writer))


async def main_handler(reader: StreamReader, writer: StreamWriter, timeout=30):
    async def session():
        try:
            async with async_timeout.timeout(30):
                with closing(writer):
                    data = await reader.readuntil(b'\r\n\r\n')
                    addr = writer.get_extra_info('peername')

                    print(f"Received {data} from {addr!r}")
                    request = RawHTTPParser(data)
                    print(f'Request: {str(request)}')

                    if request.is_parse_error:
                        print('Parse Error')
                    elif request.method == 'CONNECT':  # https
                        await https_handler(reader, writer, request)
                    else:
                        print(f'{request.method} method is not supported')
        except asyncio.TimeoutError:
            print('Timeout')

        print('Closed connection')

    asyncio.create_task(session())


async def main():
    host, port = '127.0.0.1', 4545

    server = await asyncio.start_server(
        main_handler, host, port
    )
    server.sockets[0].setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    addr = server.sockets[0].getsockname()

    print(f'Serving on {addr}')

    async with server:
        await server.serve_forever()


asyncio.run(main())