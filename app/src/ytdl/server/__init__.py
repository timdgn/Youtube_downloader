"""Process entry points for the Docker deployment (Raspberry Pi).

The handlers in ``ytdl.api`` and ``ytdl.bot`` are written against API Gateway
events and the worker against Lambda's SQS payload. The modules here supply the
host-shaped equivalents, so the deployment changes where the bytes come from
and never what the service does.
"""
