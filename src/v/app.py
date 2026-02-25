class App():
    def __init__(self):
        self.title = "Qonvo"

        # 노드/엣지 관리 (데이터만)
        self.scene = None
        self.nodes = {}      # node_id → ChatNodeWidget
        self.edges = []      # EdgeItem 리스트
        self._next_id = 1
        self.on_send = None    # 메시지 전송 콜백

    def bind(self, scene):
        """씬 바인딩"""
        self.scene = scene

    def get_node(self, node_id):
        """노드 조회"""
        return self.nodes.get(node_id)

    def clear(self):
        """모든 노드/엣지 삭제 (데이터만)"""
        self.nodes.clear()
        self.edges.clear()
        self._next_id = 1
