class APIError(Exception):
    def __init__(self, status, code, message, retryable=False):
        self.status = status
        self.body = {"code": code, "message": message, "retryable": retryable}
