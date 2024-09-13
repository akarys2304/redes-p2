import asyncio
import random
import math
from tcputils import *
import time

class Servidor:
    def __init__(self, rede, porta):
        self.rede = rede
        self.porta = porta
        self.conexoes = {}
        self.callback = None
        self.rede.registrar_recebedor(self._rdt_rcv)

    def registrar_monitor_de_conexoes_aceitas(self, callback):
        """
        Usado pela camada de aplicação para registrar uma função para ser chamada
        sempre que uma nova conexão for aceita
        """
        self.callback = callback

    def _rdt_rcv(self, src_addr, dst_addr, segment):
        src_port, dst_port, seq_no, ack_no, \
            flags, window_size, checksum, urg_ptr = read_header(segment)

        if dst_port != self.porta:
            # Ignora segmentos que não são destinados à porta do nosso servidor
            return
        if not self.rede.ignore_checksum and calc_checksum(segment, src_addr, dst_addr) != 0:
            print('descartando segmento com checksum incorreto')
            return

        payload = segment[4*(flags>>12):]
        id_conexao = (src_addr, src_port, dst_addr, dst_port)

        if (flags & FLAGS_SYN) == FLAGS_SYN:
            # A flag SYN estar setada significa que é um cliente tentando estabelecer uma conexão nova
            conexao = self.conexoes[id_conexao] = Conexao(self, id_conexao, seq_no)
            # Enviar SYN+ACK
            ack_no = seq_no + 1
            seq_no = conexao.seq_no
            header = make_header(self.porta, src_port, seq_no, ack_no, FLAGS_SYN | FLAGS_ACK)
            self.rede.enviar(fix_checksum(header, src_addr, dst_addr), src_addr)
            if self.callback:
                self.callback(conexao)
        elif id_conexao in self.conexoes:
            # Passa para a conexão adequada se ela já estiver estabelecida
            self.conexoes[id_conexao]._rdt_rcv(seq_no, ack_no, flags, payload)
        else:
            print('%s:%d -> %s:%d (pacote associado a conexão desconhecida)' %
                  (src_addr, src_port, dst_addr, dst_port))

class Conexao:
    def __init__(self, servidor, id_conexao, seq_no):
        self.servidor = servidor
        self.id_conexao = id_conexao
        self.seq_no = random.randint(0, 0xffff)  # Seq no para a próxima mensagem que enviaremos
        self.ack_no = seq_no + 1  # Número de ACK que estamos esperando receber
        self.callback = None
        self.timer = None
        self.timer_value = 1
        self.not_yet_acked = []
        self.devRTT = 0
        self.estimatedRTT = 0
        self.window = 1
        self.to_be_sent = []
    
    def _exemplo_timer(self):
        # Esta função é só um exemplo e pode ser removida
        if len(self.not_yet_acked) > 0 :
            self.not_yet_acked[0][2] = 0
            self.window = math.ceil(self.window/2)
            print("Retransmitindo...")
            self.servidor.rede.enviar(self.not_yet_acked[0][0], self.not_yet_acked[0][1])
            print("Indo pro if")
            if self.timer is not None:
                print("Está no not None")
                self.timer.cancel()
            else:
                print("Entrou aqui")
                self.timer = asyncio.get_event_loop().call_later(self.timer_value, self._exemplo_timer)
            

    def _rdt_rcv(self, seq_no, ack_no, flags, payload):
        if seq_no > self.ack_no - 1 and (flags & FLAGS_ACK == FLAGS_ACK) and len(self.not_yet_acked) > 0:
            if self.timer is not None:
                self.timer.cancel()
                if self.not_yet_acked[0][2] != 0:
                    if self.not_yet_acked[0][3] == MSS:
                        self.window += 1
                    rtt = time.time() - self.not_yet_acked[0][2]
                    if self.devRTT == 0 and self.estimatedRTT == 0:
                        self.estimatedRTT = rtt
                        self.devRTT = rtt/2
                    else:
                        self.estimatedRTT = (1 - 0.125) * self.estimatedRTT + 0.125 * rtt #6
                        self.devRTT = (1 - 0.25) * self.devRTT + 0.25 * abs(rtt - self.estimatedRTT)
                    self.timer_value = self.estimatedRTT + 4 * self.devRTT
                self.not_yet_acked.pop(0)
                self.send()
            print("Passou pelo _rdt_rcv")
            self.timer = asyncio.get_event_loop().call_later(self.timer_value, self.timer)

        # Verificar se o segmento é duplicado ou está fora de ordem
        if seq_no != self.ack_no and len(payload) != 0:
            # Se o segmento está fora de ordem, não o processamos
            return

        # Atualizar o número de confirmação esperado
        self.ack_no = seq_no + len(payload)

        # Enviar ACK de confirmação
        if len(payload) > 0 or (flags & FLAGS_SYN): #1
            src_port, dst_port = self.id_conexao[1], self.id_conexao[3]
            header = make_header(dst_port, src_port, self.seq_no, self.ack_no, FLAGS_ACK)
            src_addr, dst_addr = self.id_conexao[2], self.id_conexao[0]
            self.servidor.rede.enviar(fix_checksum(header, src_addr, dst_addr), src_addr)
        
        if flags & FLAGS_FIN: #4
            src_port, dst_port = self.id_conexao[1], self.id_conexao[3]
            header = make_header(dst_port, src_port, self.seq_no, self.ack_no + 1, FLAGS_ACK)
            src_addr, dst_addr = self.id_conexao[2], self.id_conexao[0]
            self.servidor.rede.enviar(fix_checksum(header, src_addr, dst_addr), src_addr)

            self.callback(self, b'') #Finaliza na camada de app

        # Passar os dados para a camada de aplicação
        if self.callback and payload:
            self.callback(self, payload)

    # Os métodos abaixo fazem parte da API

    def registrar_recebedor(self, callback):
        """
        Usado pela camada de aplicação para registrar uma função para ser chamada
        sempre que dados forem corretamente recebidos
        """
        self.callback = callback

    def enviar(self, dados):
        """
        Usado pela camada de aplicação para enviar dados
        """

        total_data_length = len(dados)
        bytes_sent = 0

        src_port, dst_port = self.id_conexao[1], self.id_conexao[3]
        src_addr, dst_addr = self.id_conexao[2], self.id_conexao[0]

        while bytes_sent < total_data_length: #caso o dados seja maior que MSS
            segment_size = min(MSS, total_data_length - bytes_sent)
            segment_data = dados[bytes_sent:bytes_sent + segment_size]

            header = make_header(dst_port, src_port, self.seq_no + 1, self.ack_no, FLAGS_ACK)
            msg = [fix_checksum(header + segment_data, src_addr, dst_addr), src_addr, time.time(), segment_size]

            self.seq_no += segment_size  # Incrementar seq_no após o envio
            bytes_sent += segment_size

            self.to_be_sent.append(msg)
        
        self.send()


    pass

    def fechar(self):
        """
        Usado pela camada de aplicação para fechar a conexão
        """

        src_port, dst_port = self.id_conexao[1], self.id_conexao[3]
        src_addr, dst_addr = self.id_conexao[2], self.id_conexao[0]
        header = make_header(dst_port, src_port, self.seq_no + 1, self.ack_no, FLAGS_FIN)
        self.servidor.rede.enviar(fix_checksum(header, src_addr, dst_addr), src_addr)
        self.callback = None # Ta certo isso???
        pass

    def send(self):
        for i in range(self.window):
            if len(self.to_be_sent) > 0:
                msg = self.to_be_sent.pop(0)
                self.not_yet_acked.append(msg)
                self.servidor.rede.enviar(msg[0], msg[1])

        if len(self.not_yet_acked) > 0:
            print("No send")
            self.timer = asyncio.get_event_loop().call_later(self.timer_value, self._exemplo_timer)
        pass
