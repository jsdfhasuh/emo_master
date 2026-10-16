from emo_master.apps.designer.services.operator_icon_worker import OperatorIconWorker


class OperatorCatalogWorker(OperatorIconWorker):
    def __init__(self, runtimeClient) -> None:
        super().__init__(runtimeClient, workers=1, capacity=1)
        self.currentRequest: tuple[str, int] | None = None
        self._sequence = 0

    def refresh(self, scope: str) -> tuple[str, int]:
        if self.currentRequest is not None and self.currentRequest[0] == scope:
            return self.currentRequest
        if self.currentRequest is not None:
            self.cancel(self.currentRequest)
        self._sequence += 1
        self.currentRequest = (scope, self._sequence)
        self.request(self.currentRequest, operation="catalog", timeoutMs=5000)
        return self.currentRequest

    def poll(self, maximum: int = 1):
        results = super().poll(maximum)
        valid = [result for result in results if result.key == self.currentRequest]
        if valid:
            self.currentRequest = None
        return valid
