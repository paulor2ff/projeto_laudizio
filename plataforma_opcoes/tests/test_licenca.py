"""Testes para licenca.py — assinatura Ed25519, estágios, cooldown, gating."""

import json
import threading
from pathlib import Path

import pytest


class TestEstagios:
    def test_licenca_valida_e_estagio_ok(self, licenca_temp):
        token = licenca_temp.emitir(dias_validade=30)
        arq = Path("/tmp/lic_ok.json")
        arq.write_text(json.dumps(token))
        estado = licenca_temp.modulo.importar_licenca(str(arq))
        assert estado.estagio == "ok"
        arq.unlink()

    def test_vencido_dentro_da_carencia(self, licenca_temp):
        token = licenca_temp.emitir(vencido_ha=3)  # padrão: carência = 7 dias
        arq = Path("/tmp/lic_carencia.json")
        arq.write_text(json.dumps(token))
        estado = licenca_temp.modulo.importar_licenca(str(arq))
        assert estado.estagio == "carencia"
        arq.unlink()

    def test_vencido_alem_da_carencia_fica_degradado(self, licenca_temp):
        token = licenca_temp.emitir(vencido_ha=15)  # entre 7 e 30 dias
        arq = Path("/tmp/lic_degradado.json")
        arq.write_text(json.dumps(token))
        estado = licenca_temp.modulo.importar_licenca(str(arq))
        assert estado.estagio == "degradado"
        arq.unlink()

    def test_vencido_muito_tempo_fica_bloqueado(self, licenca_temp):
        token = licenca_temp.emitir(vencido_ha=45)  # > 30 dias
        arq = Path("/tmp/lic_bloqueado.json")
        arq.write_text(json.dumps(token))
        estado = licenca_temp.modulo.importar_licenca(str(arq))
        assert estado.estagio == "bloqueado"
        arq.unlink()

    def test_sem_arquivo_de_licenca(self, licenca_temp):
        estado = licenca_temp.modulo.verificar_licenca(forcar_reverificacao=True)
        assert estado.estagio == "sem_licenca"

    def test_dados_do_payload_propagados_no_estado(self, licenca_temp):
        token = licenca_temp.emitir(cliente_id="cli_xyz", plano="premium", dias_validade=10)
        arq = Path("/tmp/lic_dados.json")
        arq.write_text(json.dumps(token))
        estado = licenca_temp.modulo.importar_licenca(str(arq))
        assert estado.cliente_id == "cli_xyz"
        assert estado.plano == "premium"
        assert estado.valido_ate is not None
        arq.unlink()


class TestAssinatura:
    def test_token_corrompido_e_rejeitado_na_importacao(self, licenca_temp):
        token = licenca_temp.emitir(dias_validade=30, corromper=True)
        arq = Path("/tmp/lic_corrompido.json")
        arq.write_text(json.dumps(token))
        with pytest.raises(ValueError, match="inválida"):
            licenca_temp.modulo.importar_licenca(str(arq))
        arq.unlink()

    def test_payload_adulterado_apos_importacao_e_detectado(self, licenca_temp):
        """
        Simula alguém editando manualmente o ficheiro licenca.json em disco
        para estender a validade — a assinatura deixa de corresponder ao
        payload modificado, e a verificação seguinte deve rejeitar.
        """
        token = licenca_temp.emitir(vencido_ha=45, dias_validade=30)  # já bloqueado
        arq = Path("/tmp/lic_para_adulterar.json")
        arq.write_text(json.dumps(token))
        licenca_temp.modulo.importar_licenca(str(arq))

        # Adulteração directa do ficheiro em cache, tentando "renovar" sem assinatura válida
        dados = json.loads(licenca_temp.modulo.LICENCA_FILE.read_text())
        dados["payload"]["valido_ate"] = "2099-01-01T00:00:00"
        licenca_temp.modulo.LICENCA_FILE.write_text(json.dumps(dados))

        estado = licenca_temp.modulo.verificar_licenca(forcar_reverificacao=True)
        assert estado.estagio == "sem_licenca"
        assert "inválida" in estado.motivo.lower() or "assinatura" in estado.motivo.lower()
        arq.unlink()

    def test_arquivo_de_licenca_inexistente_levanta_filenotfound(self, licenca_temp):
        with pytest.raises(FileNotFoundError):
            licenca_temp.modulo.importar_licenca("/tmp/nao_existe_12345.json")

    def test_token_sem_campo_valido_ate_e_rejeitado(self, licenca_temp):
        token = licenca_temp.emitir(dias_validade=30)
        del token["payload"]["valido_ate"]
        # Reassinar não é possível sem a chave (propositalmente) — a
        # assinatura original não cobre o payload sem o campo, então
        # a verificação de assinatura já falha primeiro.
        arq = Path("/tmp/lic_sem_campo.json")
        arq.write_text(json.dumps(token))
        with pytest.raises(ValueError):
            licenca_temp.modulo.importar_licenca(str(arq))
        arq.unlink()


class TestGatingDecorator:
    def test_bloqueia_quando_estagio_insuficiente(self, licenca_temp):
        token = licenca_temp.emitir(vencido_ha=45)  # bloqueado
        arq = Path("/tmp/lic_gate_bloq.json")
        arq.write_text(json.dumps(token))
        licenca_temp.modulo.importar_licenca(str(arq))

        @licenca_temp.modulo.requer_licenca(minimo="degradado")
        def funcao_paga():
            return "executou"

        with pytest.raises(licenca_temp.modulo.LicencaError):
            funcao_paga()
        arq.unlink()

    def test_permite_quando_estagio_suficiente(self, licenca_temp):
        token = licenca_temp.emitir(vencido_ha=3)  # carência — dentro do mínimo "degradado"
        arq = Path("/tmp/lic_gate_ok.json")
        arq.write_text(json.dumps(token))
        licenca_temp.modulo.importar_licenca(str(arq))

        @licenca_temp.modulo.requer_licenca(minimo="degradado")
        def funcao_paga():
            return "executou"

        assert funcao_paga() == "executou"
        arq.unlink()

    def test_excecao_carrega_o_estado_anexado(self, licenca_temp):
        token = licenca_temp.emitir(vencido_ha=45)
        arq = Path("/tmp/lic_gate_estado.json")
        arq.write_text(json.dumps(token))
        licenca_temp.modulo.importar_licenca(str(arq))

        @licenca_temp.modulo.requer_licenca(minimo="ok")
        def funcao_premium():
            return "executou"

        with pytest.raises(licenca_temp.modulo.LicencaError) as exc_info:
            funcao_premium()
        assert exc_info.value.estado.estagio == "bloqueado"
        arq.unlink()

    def test_nivel_minimo_invalido_levanta_valueerror(self, licenca_temp):
        with pytest.raises(ValueError):
            licenca_temp.modulo.requer_licenca(minimo="nao_existe")

    @pytest.mark.parametrize("vencido_ha,minimo,deve_passar", [
        (None, "ok", True),          # válida, exige ok
        (3,    "ok", False),         # carência, exige ok → bloqueia
        (3,    "carencia", True),    # carência, exige carência → passa
        (15,   "carencia", False),   # degradado, exige carência → bloqueia
        (15,   "degradado", True),   # degradado, exige degradado → passa
        (45,   "degradado", False),  # bloqueado, exige degradado → bloqueia
    ])
    def test_matriz_de_niveis(self, licenca_temp, vencido_ha, minimo, deve_passar):
        kwargs = {"vencido_ha": vencido_ha} if vencido_ha is not None else {"dias_validade": 30}
        token = licenca_temp.emitir(**kwargs)
        arq = Path(f"/tmp/lic_matriz_{vencido_ha}_{minimo}.json")
        arq.write_text(json.dumps(token))
        licenca_temp.modulo.importar_licenca(str(arq))

        @licenca_temp.modulo.requer_licenca(minimo=minimo)
        def f():
            return True

        if deve_passar:
            assert f() is True
        else:
            with pytest.raises(licenca_temp.modulo.LicencaError):
                f()
        arq.unlink()


class TestCacheEmMemoria:
    def test_importar_licenca_invalida_o_cache_imediatamente(self, licenca_temp):
        token1 = licenca_temp.emitir(dias_validade=30)
        arq1 = Path("/tmp/lic_cache1.json")
        arq1.write_text(json.dumps(token1))
        licenca_temp.modulo.importar_licenca(str(arq1))
        assert licenca_temp.modulo.verificar_licenca().estagio == "ok"

        # Importar uma segunda licença já bloqueada — o cache deve reflectir
        # o novo estado imediatamente, sem precisar de forcar_reverificacao
        token2 = licenca_temp.emitir(vencido_ha=45)
        arq2 = Path("/tmp/lic_cache2.json")
        arq2.write_text(json.dumps(token2))
        licenca_temp.modulo.importar_licenca(str(arq2))
        assert licenca_temp.modulo.verificar_licenca().estagio == "bloqueado"

        arq1.unlink(); arq2.unlink()

    def test_cache_evita_releitura_dentro_do_ttl(self, licenca_temp):
        token = licenca_temp.emitir(dias_validade=30)
        arq = Path("/tmp/lic_ttl.json")
        arq.write_text(json.dumps(token))
        licenca_temp.modulo.importar_licenca(str(arq))

        licenca_temp.modulo.verificar_licenca()  # popula o cache em memória

        # Apagar o ficheiro directamente, sem passar por importar_licenca()
        # (que invalidaria o cache) — se o cache estiver activo, a próxima
        # chamada SEM forcar_reverificacao ainda deve devolver o estado OK.
        licenca_temp.modulo.LICENCA_FILE.unlink()
        estado2 = licenca_temp.modulo.verificar_licenca()
        assert estado2.estagio == "ok"  # ainda servido do cache em memória

        # Forçar reverificação deve agora detectar a ausência do ficheiro
        estado3 = licenca_temp.modulo.verificar_licenca(forcar_reverificacao=True)
        assert estado3.estagio == "sem_licenca"

        arq.unlink()


class TestFileLocking:
    def test_concorrencia_nao_corrompe_o_arquivo(self, licenca_temp):
        """
        Várias threads importando licenças diferentes em sequência rápida
        não devem corromper o ficheiro nem deixar lock preso — mesmo padrão
        de protecção já usado em alertas.py.
        """
        erros = []
        trava = threading.Lock()

        def worker(i):
            try:
                token = licenca_temp.emitir(cliente_id=f"cli_{i}", dias_validade=30)
                arq = Path(f"/tmp/lic_thread_{i}.json")
                arq.write_text(json.dumps(token))
                licenca_temp.modulo.importar_licenca(str(arq))
                arq.unlink()
            except Exception as exc:
                with trava:
                    erros.append(str(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert erros == []
        assert not licenca_temp.modulo._LOCK_FILE.exists()
        # O ficheiro final deve estar íntegro e correspondente a alguma das licenças
        estado_final = licenca_temp.modulo.verificar_licenca(forcar_reverificacao=True)
        assert estado_final.estagio == "ok"

    def test_escrita_atomica_sem_tmp_residual(self, licenca_temp):
        token = licenca_temp.emitir(dias_validade=30)
        arq = Path("/tmp/lic_atomico.json")
        arq.write_text(json.dumps(token))
        licenca_temp.modulo.importar_licenca(str(arq))
        tmp_path = licenca_temp.modulo.LICENCA_FILE.with_suffix(".json.tmp")
        assert not tmp_path.exists()
        arq.unlink()
