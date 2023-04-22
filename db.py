import time

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from disasm.utils import value_type_to_mode_type_str, finalize_type_to_str
from explorer.types import Message as ExplorerMessage
from node.types import *


class Database:

    def __init__(self, *, server: str, user: str, password: str, database: str, schema: str,
                 message_callback: callable):
        self.server = server
        self.user = user
        self.password = password
        self.database = database
        self.schema = schema
        self.message_callback = message_callback
        self.pool: AsyncConnectionPool | None = None

    async def connect(self):
        try:
            self.pool = AsyncConnectionPool(
                f"host={self.server} user={self.user} password={self.password} dbname={self.database} "
                f"options=-csearch_path={self.schema}",
                kwargs={
                    "row_factory": dict_row,
                    "autocommit": True,
                },
                max_size=16,
            )
        except Exception as e:
            await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseConnectError, e))
            return
        await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseConnected, None))

    @staticmethod
    async def _insert_transition(conn: psycopg.AsyncConnection, exe_tx_db_id: int | None, fee_db_id: int | None,
                                 transition: Transition, ts_index: int):
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO transition (transition_id, transaction_execute_id, fee_id, program_id, "
                "function_name, proof, tpk, tcm, index) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (str(transition.id), exe_tx_db_id, fee_db_id, str(transition.program_id),
                str(transition.function_name), str(transition.proof), str(transition.tpk),
                str(transition.tcm), ts_index)
            )
            transition_db_id = (await cur.fetchone())["id"]

            transition_input: TransitionInput
            for input_index, transition_input in enumerate(transition.inputs):
                await cur.execute(
                    "INSERT INTO transition_input (transition_id, type, index) VALUES (%s, %s, %s) RETURNING id",
                    (transition_db_id, transition_input.type.name, input_index)
                )
                transition_input_db_id = (await cur.fetchone())["id"]
                match transition_input.type:
                    case TransitionInput.Type.Public:
                        transition_input: PublicTransitionInput
                        await cur.execute(
                            "INSERT INTO transition_input_public (transition_input_id, plaintext_hash, plaintext) "
                            "VALUES (%s, %s, %s)",
                            (transition_input_db_id, str(transition_input.plaintext_hash),
                            transition_input.plaintext.dump_nullable())
                        )
                    case TransitionInput.Type.Private:
                        transition_input: PrivateTransitionInput
                        await cur.execute(
                            "INSERT INTO transition_input_private (transition_input_id, ciphertext_hash, ciphertext) "
                            "VALUES (%s, %s, %s)",
                            (transition_input_db_id, str(transition_input.ciphertext_hash),
                            transition_input.ciphertext.dumps())
                        )
                    case TransitionInput.Type.Record:
                        transition_input: RecordTransitionInput
                        await cur.execute(
                            "INSERT INTO transition_input_record (transition_input_id, serial_number, tag) "
                            "VALUES (%s, %s, %s)",
                            (transition_input_db_id, str(transition_input.serial_number),
                            str(transition_input.tag))
                        )
                    case TransitionInput.Type.ExternalRecord:
                        transition_input: ExternalRecordTransitionInput
                        await cur.execute(
                            "INSERT INTO transition_input_external_record (transition_input_id, commitment) "
                            "VALUES (%s, %s)",
                            (transition_input_db_id, str(transition_input.input_commitment))
                        )

                    case _:
                        raise NotImplementedError

            transition_output: TransitionOutput
            for output_index, transition_output in enumerate(transition.outputs):
                await cur.execute(
                    "INSERT INTO transition_output (transition_id, type, index) VALUES (%s, %s, %s) RETURNING id",
                    (transition_db_id, transition_output.type.name, output_index)
                )
                transition_output_db_id = (await cur.fetchone())["id"]
                match transition_output.type:
                    case TransitionOutput.Type.Public:
                        transition_output: PublicTransitionOutput
                        await cur.execute(
                            "INSERT INTO transition_output_public (transition_output_id, plaintext_hash, plaintext) "
                            "VALUES (%s, %s, %s)",
                            (transition_output_db_id, str(transition_output.plaintext_hash),
                            transition_output.plaintext.dump_nullable())
                        )
                    case TransitionOutput.Type.Private:
                        transition_output: PrivateTransitionOutput
                        await cur.execute(
                            "INSERT INTO transition_output_private (transition_output_id, ciphertext_hash, ciphertext) "
                            "VALUES (%s, %s, %s)",
                            (transition_output_db_id, str(transition_output.ciphertext_hash),
                            transition_output.ciphertext.dumps())
                        )
                    case TransitionOutput.Type.Record:
                        transition_output: RecordTransitionOutput
                        await cur.execute(
                            "INSERT INTO transition_output_record (transition_output_id, commitment, checksum, record_ciphertext) "
                            "VALUES (%s, %s, %s, %s)",
                            (transition_output_db_id, str(transition_output.commitment),
                            str(transition_output.checksum), transition_output.record_ciphertext.dumps())
                        )
                    case TransitionOutput.Type.ExternalRecord:
                        transition_output: ExternalRecordTransitionOutput
                        await cur.execute(
                            "INSERT INTO transition_output_external_record (transition_output_id, commitment) "
                            "VALUES (%s, %s)",
                            (transition_output_db_id, str(transition_output.commitment))
                        )
                    case _:
                        raise NotImplementedError

            if transition.finalize.value is not None:
                for finalize_index, finalize in enumerate(transition.finalize.value):
                    await cur.execute(
                       "INSERT INTO transition_finalize (transition_id, type, index) VALUES (%s, %s, %s) "
                       "RETURNING id",
                        (transition_db_id, finalize.type.name, finalize_index)
                    )
                    transition_finalize_db_id = (await cur.fetchone())["id"]
                    match finalize.type:
                        case Value.Type.Plaintext:
                            finalize: PlaintextValue
                            await cur.execute(
                                "INSERT INTO transition_finalize_plaintext (transition_finalize_id, plaintext) "
                                "VALUES (%s, %s)",
                                (transition_finalize_db_id, finalize.plaintext.dump())
                            )
                        case Value.Type.Record:
                            finalize: RecordValue
                            await cur.execute(
                                "INSERT INTO transition_finalize_record (transition_finalize_id, record) "
                                "VALUES (%s, %s)",
                                (transition_finalize_db_id, str(finalize.record))
                            )

            if str(transition.program_id) != "credits.aleo":
                await cur.execute(
                    "SELECT id FROM program WHERE program_id = %s", (str(transition.program_id),)
                )
                program_db_id = (await cur.fetchone())["id"]
                await cur.execute(
                    "UPDATE program_function SET called = called + 1 WHERE program_id = %s AND name = %s",
                    (program_db_id, str(transition.function_name))
                )

    async def _save_block(self, block: Block):
        async with self.pool.connection() as conn:
            conn: psycopg.AsyncConnection
            async with conn.transaction():
                async with conn.cursor() as cur:
                    try:
                        await cur.execute(
                            "INSERT INTO block (height, block_hash, previous_hash, previous_state_root, transactions_root, "
                            "coinbase_accumulator_point, round, coinbase_target, proof_target, last_coinbase_target, "
                            "last_coinbase_timestamp, timestamp, signature, total_supply, cumulative_proof_target, "
                            "finalize_root) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                            "RETURNING id",
                            (block.header.metadata.height, str(block.block_hash), str(block.previous_hash),
                            str(block.header.previous_state_root), str(block.header.transactions_root),
                            str(block.header.coinbase_accumulator_point), block.header.metadata.round,
                            block.header.metadata.coinbase_target, block.header.metadata.proof_target,
                            block.header.metadata.last_coinbase_target, block.header.metadata.last_coinbase_timestamp,
                            block.header.metadata.timestamp, str(block.signature),
                            block.header.metadata.total_supply_in_microcredits,
                            block.header.metadata.cumulative_proof_target, str(block.header.finalize_root))
                        )
                        block_db_id = (await cur.fetchone())["id"]

                        transaction: Transaction
                        for tx_index, transaction in enumerate(block.transactions):
                            match transaction.type:
                                case Transaction.Type.Deploy:
                                    transaction: DeployTransaction
                                    transaction_id = transaction.id
                                    await cur.execute(
                                        "INSERT INTO transaction (block_id, transaction_id, type, index) VALUES (%s, %s, %s, %s) RETURNING id",
                                        (block_db_id, str(transaction_id), transaction.type.name, tx_index)
                                    )
                                    transaction_db_id = (await cur.fetchone())["id"]
                                    await cur.execute(
                                        "INSERT INTO transaction_deploy (transaction_id, edition) "
                                        "VALUES (%s, %s) RETURNING id",
                                        (transaction_db_id, transaction.deployment.edition)
                                    )
                                    deploy_transaction_db_id = (await cur.fetchone())["id"]

                                    program: Program = transaction.deployment.program
                                    imports = [str(x.program_id) for x in program.imports]
                                    mappings = list(map(str, program.mappings.keys()))
                                    interfaces = list(map(str, program.interfaces.keys()))
                                    records = list(map(str, program.records.keys()))
                                    closures = list(map(str, program.closures.keys()))
                                    functions = list(map(str, program.functions.keys()))
                                    await cur.execute(
                                        "INSERT INTO program "
                                        "(transaction_deploy_id, program_id, import, mapping, interface, record, "
                                        "closure, function, raw_data, is_helloworld, feature_hash, owner, signature) "
                                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                                        (deploy_transaction_db_id, str(program.id), imports, mappings, interfaces, records,
                                        closures, functions, program.dump(), program.is_helloworld(), program.feature_hash(),
                                        str(transaction.owner.address), str(transaction.owner.signature))
                                    )
                                    program_db_id = (await cur.fetchone())["id"]
                                    for function in program.functions.values():
                                        inputs = []
                                        input_modes = []
                                        i: FunctionInput
                                        for i in function.inputs:
                                            mode, _type = value_type_to_mode_type_str(i.value_type)
                                            inputs.append(_type)
                                            input_modes.append(mode)
                                        outputs = []
                                        output_modes = []
                                        o: FunctionOutput
                                        for o in function.outputs:
                                            mode, _type = value_type_to_mode_type_str(o.value_type)
                                            outputs.append(_type)
                                            output_modes.append(mode)
                                        finalizes = []
                                        if function.finalize.value is not None:
                                            f: FinalizeInput
                                            for f in function.finalize.value[1].inputs:
                                                finalizes.append(finalize_type_to_str(f.finalize_type))
                                        await cur.execute(
                                            "INSERT INTO program_function (program_id, name, input, input_mode, output, output_mode, finalize) "
                                            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                            (program_db_id, str(function.name), inputs, input_modes, outputs, output_modes, finalizes)
                                        )

                                    await cur.execute(
                                        "INSERT INTO fee (transaction_id, global_state_root, inclusion_proof) "
                                        "VALUES (%s, %s, %s) RETURNING id",
                                        (transaction_db_id, str(transaction.fee.global_state_root), transaction.fee.inclusion_proof.dumps())
                                    )
                                    fee_db_id = (await cur.fetchone())["id"]
                                    await self._insert_transition(conn, None, fee_db_id, transaction.fee.transition, 0)

                                case Transaction.Type.Execute:
                                    transaction: ExecuteTransaction
                                    transaction_id = transaction.id
                                    await cur.execute(
                                        "INSERT INTO transaction (block_id, transaction_id, type, index) VALUES (%s, %s, %s, %s) RETURNING id",
                                        (block_db_id, str(transaction_id), transaction.type.name, tx_index)
                                    )
                                    transaction_db_id = (await cur.fetchone())["id"]
                                    await cur.execute(
                                        "INSERT INTO transaction_execute (transaction_id, global_state_root, inclusion_proof) "
                                        "VALUES (%s, %s, %s) RETURNING id",
                                        (transaction_db_id, str(transaction.execution.global_state_root),
                                        transaction.execution.inclusion_proof.dumps())
                                    )
                                    execute_transaction_db_id = (await cur.fetchone())["id"]

                                    transition: Transition
                                    for ts_index, transition in enumerate(transaction.execution.transitions):
                                        await self._insert_transition(conn, execute_transaction_db_id, None, transition, ts_index)

                                    if transaction.additional_fee.value is not None:
                                        fee: Fee = transaction.additional_fee.value
                                        await cur.execute(
                                            "INSERT INTO fee (transaction_id, global_state_root, inclusion_proof) "
                                            "VALUES (%s, %s, %s) RETURNING id",
                                            (transaction_db_id, str(fee.global_state_root), fee.inclusion_proof.dumps())
                                        )
                                        fee_db_id = (await cur.fetchone())["id"]
                                        await self._insert_transition(conn, None, fee_db_id, fee.transition, 0)


                        if block.coinbase.value is not None:
                            coinbase_reward = block.get_coinbase_reward((await self.get_latest_block()).header.metadata.last_coinbase_timestamp)
                            await cur.execute(
                                "UPDATE block SET coinbase_reward = %s WHERE id = %s",
                                (coinbase_reward, block_db_id)
                            )
                            partial_solutions = list(block.coinbase.value.partial_solutions)
                            solutions = []
                            partial_solutions = list(zip(partial_solutions,
                                                    [partial_solution.commitment.to_target() for partial_solution in
                                                     partial_solutions]))
                            target_sum = sum(target for _, target in partial_solutions)
                            partial_solution: PartialSolution
                            for partial_solution, target in partial_solutions:
                                solutions.append((partial_solution, target, coinbase_reward * target // (2 * target_sum)))

                            await cur.execute(
                                "INSERT INTO coinbase_solution (block_id, proof_x, proof_y_positive, target_sum) "
                                "VALUES (%s, %s, %s, %s) RETURNING id",
                                (block_db_id, str(block.coinbase.value.proof.w.x), block.coinbase.value.proof.w.flags, target_sum)
                            )
                            coinbase_solution_db_id = (await cur.fetchone())["id"]
                            await cur.execute("SELECT total_credit FROM leaderboard_total")
                            current_total_credit = (await cur.fetchone())["total_credit"]
                            if current_total_credit is None:
                                await cur.execute("INSERT INTO leaderboard_total (total_credit) VALUES (0)")
                                current_total_credit = 0
                            partial_solution: PartialSolution
                            for partial_solution, target, reward in solutions:
                                await cur.execute(
                                    "INSERT INTO partial_solution (coinbase_solution_id, address, nonce, commitment, target, reward) "
                                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                                    (coinbase_solution_db_id, str(partial_solution.address), partial_solution.nonce,
                                    str(partial_solution.commitment), partial_solution.commitment.to_target(), reward)
                                )
                                if reward > 0:
                                    await cur.execute(
                                        "INSERT INTO leaderboard (address, total_reward) VALUES (%s, %s) "
                                        "ON CONFLICT (address) DO UPDATE SET total_reward = leaderboard.total_reward + %s",
                                        (str(partial_solution.address), reward)
                                    )
                                    if block.header.metadata.height >= 130888 and block.header.metadata.timestamp < 1675209600 and current_total_credit < 37_500_000_000_000:
                                        await cur.execute(
                                            "UPDATE leaderboard SET total_incentive = leaderboard.total_incentive + %s WHERE address = %s",
                                            (reward, str(partial_solution.address))
                                        )
                            if block.header.metadata.height >= 130888 and block.header.metadata.timestamp < 1675209600 and current_total_credit < 37_500_000_000_000:
                                await cur.execute(
                                    "UPDATE leaderboard_total SET total_credit = leaderboard_total.total_credit + %s",
                                    (sum(reward for _, _, reward in solutions),)
                                )

                        await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseBlockAdded, block.header.metadata.height))
                    except Exception as e:
                        await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                        breakpoint()
                        raise

    async def save_block(self, block: Block):
        await self._save_block(block)

    @staticmethod
    def _get_block_header(block: dict):
        return BlockHeader(
            previous_state_root=Field.loads(block["previous_state_root"]),
            transactions_root=Field.loads(block["transactions_root"]),
            coinbase_accumulator_point=Field.loads(block["coinbase_accumulator_point"]),
            metadata=BlockHeaderMetadata(
                network=u16(3),
                round_=u64(block["round"]),
                height=u32(block["height"]),
                coinbase_target=u64(block["coinbase_target"]),
                proof_target=u64(block["proof_target"]),
                last_coinbase_target=u64(block["last_coinbase_target"]),
                last_coinbase_timestamp=i64(block["last_coinbase_timestamp"]),
                timestamp=i64(block["timestamp"]),
            )
        )

    @staticmethod
    async def _get_transition(transition: dict, conn: psycopg.AsyncConnection):
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM transition_input WHERE transition_id = %s",
                (transition["id"],)
            )
            transition_inputs = await cur.fetchall()
            tis = []
            for transition_input in transition_inputs:
                match transition_input["type"]:
                    case TransitionInput.Type.Public.name:
                        await cur.execute(
                            "SELECT * FROM transition_input_public WHERE transition_input_id = %s",
                            (transition_input["id"],)
                        )
                        transition_input_public = await cur.fetchone()
                        if transition_input_public["plaintext"] is None:
                            plaintext = None
                        else:
                            plaintext = Plaintext.load(bytearray(transition_input_public["plaintext"]))
                        tis.append((PublicTransitionInput(
                            plaintext_hash=Field.loads(transition_input_public["plaintext_hash"]),
                            plaintext=Option[Plaintext](plaintext)
                        ), transition_input["index"]))

                    case TransitionInput.Type.Private.name:
                        await cur.execute(
                            "SELECT * FROM transition_input_private WHERE transition_input_id = %s",
                            (transition_input["id"],)
                        )
                        transition_input_private = await cur.fetchone()
                        if transition_input_private["ciphertext"] is None:
                            ciphertext = None
                        else:
                            ciphertext = Ciphertext.loads(transition_input_private["ciphertext"])
                        tis.append((PrivateTransitionInput(
                            ciphertext_hash=Field.loads(transition_input_private["ciphertext_hash"]),
                            ciphertext=Option[Ciphertext](ciphertext)
                        ), transition_input["index"]))

                    case TransitionInput.Type.Record.name:
                        await cur.execute(
                            "SELECT * FROM transition_input_record WHERE transition_input_id = %s",
                            (transition_input["id"],)
                        )
                        transition_input_record = await cur.fetchone()
                        tis.append((RecordTransitionInput(
                            serial_number=Field.loads(transition_input_record["serial_number"]),
                            tag=Field.loads(transition_input_record["tag"])
                        ), transition_input["index"]))

                    case TransitionInput.Type.ExternalRecord.name:
                        await cur.execute(
                            "SELECT * FROM transition_input_external_record WHERE transition_input_id = %s",
                            (transition_input["id"],)
                        )
                        transition_input_external_record = await cur.fetchone()
                        tis.append((ExternalRecordTransitionInput(
                            input_commitment=Field.loads(transition_input_external_record["commitment"]),
                        ), transition_input["index"]))

                    case _:
                        raise NotImplementedError
            tis.sort(key=lambda x: x[1])
            tis = [x[0] for x in tis]

            await cur.execute(
                "SELECT * FROM transition_output WHERE transition_id = %s",
                (transition["id"],)
            )
            transition_outputs = await cur.fetchall()
            tos = []
            for transition_output in transition_outputs:
                match transition_output["type"]:
                    case TransitionOutput.Type.Public.name:
                        await cur.execute(
                            "SELECT * FROM transition_output_public WHERE transition_output_id = %s",
                            (transition_output["id"],)
                        )
                        transition_output_public = await cur.fetchone()
                        if transition_output_public["plaintext"] is None:
                            plaintext = None
                        else:
                            plaintext = Plaintext.load(bytearray(transition_output_public["plaintext"]))
                        tos.append((PublicTransitionOutput(
                            plaintext_hash=Field.loads(transition_output_public["plaintext_hash"]),
                            plaintext=Option[Plaintext](plaintext)
                        ), transition_output["index"]))
                    case TransitionOutput.Type.Private.name:
                        await cur.execute(
                            "SELECT * FROM transition_output_private WHERE transition_output_id = %s",
                            (transition_output["id"],)
                        )
                        transition_output_private = await cur.fetchone()
                        if transition_output_private["ciphertext"] is None:
                            ciphertext = None
                        else:
                            ciphertext = Ciphertext.loads(transition_output_private["ciphertext"])
                        tos.append((PrivateTransitionOutput(
                            ciphertext_hash=Field.loads(transition_output_private["ciphertext_hash"]),
                            ciphertext=Option[Ciphertext](ciphertext)
                        ), transition_output["index"]))
                    case TransitionOutput.Type.Record.name:
                        await cur.execute(
                            "SELECT * FROM transition_output_record WHERE transition_output_id = %s",
                            (transition_output["id"],)
                        )
                        transition_output_record = await cur.fetchone()
                        if transition_output_record["record_ciphertext"] is None:
                            record_ciphertext = None
                        else:
                            record_ciphertext = Record[Ciphertext].loads(transition_output_record["record_ciphertext"])
                        tos.append((RecordTransitionOutput(
                            commitment=Field.loads(transition_output_record["commitment"]),
                            checksum=Field.loads(transition_output_record["checksum"]),
                            record_ciphertext=Option[Record[Ciphertext]](record_ciphertext)
                        ), transition_output["index"]))
                    case TransitionOutput.Type.ExternalRecord.name:
                        await cur.execute(
                            "SELECT * FROM transition_output_external_record WHERE transition_output_id = %s",
                            (transition_output["id"],)
                        )
                        transition_output_external_record = await cur.fetchone()
                        tos.append((ExternalRecordTransitionOutput(
                            commitment=Field.loads(transition_output_external_record["commitment"]),
                        ), transition_output["index"]))
                    case _:
                        raise NotImplementedError
            tos.sort(key=lambda x: x[1])
            tos = [x[0] for x in tos]

            await cur.execute(
                "SELECT * FROM transition_finalize WHERE transition_id = %s",
                (transition["id"],)
            )
            transition_finalizes = await cur.fetchall()
            if len(transition_finalizes) == 0:
                finalize = None
            else:
                finalize = []
                for transition_finalize in transition_finalizes:
                    match transition_finalize["type"]:
                        case Value.Type.Plaintext.name:
                            await cur.execute(
                                "SELECT * FROM transition_finalize_plaintext WHERE transition_finalize_id = %s",
                                (transition_finalize["id"],)
                            )
                            transition_finalize_plaintext = await cur.fetchone()
                            finalize.append((PlaintextValue(
                                plaintext=Plaintext.load(bytearray(transition_finalize_plaintext["plaintext"]))
                            ), transition_finalize["index"]))
                        case Value.Type.Record.name:
                            await cur.execute(
                                "SELECT * FROM transition_finalize_record WHERE transition_finalize_id = %s",
                                (transition_finalize["id"],)
                            )
                            transition_finalize_record = await cur.fetchone()
                            finalize.append((RecordValue(
                                record=Record[Plaintext].loads(transition_finalize_record["record"])
                            ), transition_finalize["index"]))
                finalize.sort(key=lambda x: x[1])
                finalize = Vec[Value, u16]([x[0] for x in finalize])

            return Transition(
                id_=TransitionID.loads(transition["transition_id"]),
                program_id=ProgramID.loads(transition["program_id"]),
                function_name=Identifier.loads(transition["function_name"]),
                inputs=Vec[TransitionInput, u16](tis),
                outputs=Vec[TransitionOutput, u16](tos),
                finalize=Option[Vec[Value, u16]](finalize),
                proof=Proof.loads(transition["proof"]),
                tpk=Group.loads(transition["tpk"]),
                tcm=Field.loads(transition["tcm"]),
                fee=i64(transition["fee"]),
            )

    @staticmethod
    async def _get_full_block(block: dict, conn: psycopg.AsyncConnection):
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM transaction WHERE block_id = %s", (block['id'],))
            transactions = await cur.fetchall()
            txs = []
            for transaction in transactions:
                match transaction["type"]:
                    case Transaction.Type.Deploy.name:
                        await cur.execute(
                            "SELECT * FROM transaction_deploy WHERE transaction_id = %s",
                            (transaction["id"],)
                        )
                        deploy_transaction = await cur.fetchone()
                        await cur.execute(
                            "SELECT raw_data FROM program WHERE transaction_deploy_id = %s",
                            (deploy_transaction["id"],)
                        )
                        program = (await cur.fetchone())["raw_data"]
                        deployment = Deployment(
                            edition=u16(deploy_transaction["edition"]),
                            program=Program.load(bytearray(program)),
                            verifying_keys=Vec[Tuple[Identifier, VerifyingKey, Certificate], u16]([]),
                        )
                        await cur.execute(
                            "SELECT * FROM fee WHERE transaction_id = %s",
                            (transaction["id"],)
                        )
                        fee = await cur.fetchone()
                        await cur.execute(
                            "SELECT * FROM transition WHERE fee_id = %s",
                            (fee["id"],)
                        )
                        fee_transition = await cur.fetchone()
                        if fee_transition is None:
                            raise ValueError("fee transition not found")
                        proof = None
                        if fee["inclusion_proof"] is not None:
                            proof = Proof.loads(fee["inclusion_proof"])
                        fee = Fee(
                            transition=await Database._get_transition(fee_transition, conn),
                            global_state_root=StateRoot.loads(fee["global_state_root"]),
                            inclusion_proof=Option[Proof](proof),
                        )
                        txs.append(DeployTransaction(
                            id_=TransactionID.loads(transaction["transaction_id"]),
                            deployment=deployment,
                            fee=fee,
                        ))
                    case Transaction.Type.Execute.name:
                        await cur.execute(
                            "SELECT * FROM transaction_execute WHERE transaction_id = %s",
                            (transaction["id"],)
                        )
                        execute_transaction = await cur.fetchone()
                        await cur.execute(
                            "SELECT * FROM transition WHERE transaction_execute_id = %s",
                            (execute_transaction["id"],)
                        )
                        transitions = await cur.fetchall()
                        tss = []
                        for transition in transitions:
                            tss.append(await Database._get_transition(transition, conn))
                        await cur.execute(
                            "SELECT * FROM fee WHERE transaction_id = %s",
                            (transaction["id"],)
                        )
                        additional_fee = await cur.fetchone()
                        if additional_fee is None:
                            fee = None
                        else:
                            await cur.execute(
                                "SELECT * FROM transition WHERE fee_id = %s",
                                (additional_fee["id"],)
                            )
                            fee_transition = await cur.fetchone()
                            if fee_transition is None:
                                raise ValueError("fee transition not found")
                            proof = None
                            if additional_fee["inclusion_proof"] is not None:
                                proof = Proof.loads(additional_fee["inclusion_proof"])
                            fee = Fee(
                                transition=await Database._get_transition(fee_transition, conn),
                                global_state_root=StateRoot.loads(additional_fee["global_state_root"]),
                                inclusion_proof=Option[Proof](proof),
                            )
                        if execute_transaction["inclusion_proof"] is None:
                            proof = None
                        else:
                            proof = Proof.loads(execute_transaction["inclusion_proof"])
                        txs.append(ExecuteTransaction(
                            id_=TransactionID.loads(transaction["transaction_id"]),
                            execution=Execution(
                                transitions=Vec[Transition, u16](tss),
                                global_state_root=StateRoot.loads(execute_transaction["global_state_root"]),
                                inclusion_proof=Option[Proof](proof),
                            ),
                            additional_fee=Option[Fee](fee),
                        ))
                    case _:
                        raise NotImplementedError
            await cur.execute("SELECT * FROM coinbase_solution WHERE block_id = %s", (block["id"],))
            coinbase_solution = await cur.fetchone()
            if coinbase_solution is not None:
                await cur.execute(
                    "SELECT * FROM partial_solution WHERE coinbase_solution_id = %s",
                    (coinbase_solution["id"],)
                )
                partial_solutions = await cur.fetchall()
                pss = []
                for partial_solution in partial_solutions:
                    pss.append(PartialSolution.load_json(dict(partial_solution)))
                coinbase_solution = CoinbaseSolution(
                    partial_solutions=Vec[PartialSolution, u32](pss),
                    proof=KZGProof(
                        w=G1Affine(
                            x=Fq(value=int(coinbase_solution["proof_x"])),
                            # This is very wrong
                            flags=False,
                        ),
                        random_v=Option[Field](None),
                    )
                )
            else:
                coinbase_solution = None

            return Block(
                block_hash=BlockHash.loads(block['block_hash']),
                previous_hash=BlockHash.loads(block['previous_hash']),
                header=Database._get_block_header(block),
                transactions=Transactions(
                    transactions=Vec[Transaction, u32](txs),
                ),
                coinbase=Option[CoinbaseSolution](coinbase_solution),
                signature=Signature.loads(block['signature']),
            )

    @staticmethod
    async def _get_full_block_range(start: int, end: int, conn: psycopg.AsyncConnection):
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM block WHERE height <= %s AND height > %s ORDER BY height DESC",
                (start, end)
            )
            blocks = await cur.fetchall()
            return [await Database._get_full_block(block, conn) for block in blocks]

    @staticmethod
    async def _get_fast_block(block: dict, conn: psycopg.AsyncConnection) -> dict:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM transaction WHERE block_id = %s",
                (block["id"],)
            )
            transaction_count = (await cur.fetchone())["count"]
            await cur.execute(
                "SELECT COUNT(*) FROM partial_solution ps "
                "JOIN coinbase_solution cs on ps.coinbase_solution_id = cs.id "
                "WHERE cs.block_id = %s",
                (block["id"],)
            )
            partial_solution_count = (await cur.fetchone())["count"]
            return {
                **block,
                "transaction_count": transaction_count,
                "partial_solution_count": partial_solution_count,
            }

    @staticmethod
    async def _get_fast_block_range(start: int, end: int, conn: psycopg.AsyncConnection):
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM block WHERE height <= %s AND height > %s ORDER BY height DESC",
                (start, end)
            )
            blocks = await cur.fetchall()
            return [await Database._get_fast_block(block, conn) for block in blocks]

    async def get_latest_height(self):
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT height FROM block ORDER BY height DESC LIMIT 1")
                    result = await cur.fetchone()
                    if result is None:
                        return None
                    return result['height']
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_latest_block(self):
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT * FROM block ORDER BY height DESC LIMIT 1")
                    block = await cur.fetchone()
                    if block is None:
                        return None
                    return await self._get_full_block(block, conn)
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_block_by_height(self, height: u32):
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT * FROM block WHERE height = %s", (height,))
                    block = await cur.fetchone()
                    if block is None:
                        return None
                    return await self._get_full_block(block, conn)
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_block_hash_by_height(self, height: u32):
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT * FROM block WHERE height = %s", (height,))
                    block = await cur.fetchone()
                    if block is None:
                        return None
                    return BlockHash.loads(block['block_hash'])
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_block_header_by_height(self, height: u32):
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT * FROM block WHERE height = %s", (height,))
                    block = await cur.fetchone()
                    if block is None:
                        return None
                    return self._get_block_header(block)
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_block_by_hash(self, block_hash: BlockHash | str) -> Block | None:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT * FROM block WHERE block_hash = %s", (str(block_hash),))
                    block = await cur.fetchone()
                    if block is None:
                        return None
                    return await self._get_full_block(block, conn)
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_block_header_by_hash(self, block_hash: BlockHash) -> BlockHeader | None:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT * FROM block WHERE block_hash = %s", (str(block_hash),))
                    block = await cur.fetchone()
                    if block is None:
                        return None
                    return self._get_block_header(block)
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_recent_blocks_fast(self):
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            try:
                latest_height = await self.get_latest_height()
                return await Database._get_fast_block_range(latest_height, latest_height - 30, conn)
            except Exception as e:
                await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                raise

    async def get_validator_from_block_hash(self, block_hash: BlockHash) -> Address | None:
        raise NotImplementedError
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            try:
                return await conn.fetchval(
                    "SELECT owner "
                    "FROM explorer.record r "
                    "JOIN explorer.transition ts ON r.output_transition_id = ts.id "
                    "JOIN explorer.transaction tx ON ts.transaction_id = tx.id "
                    "JOIN explorer.block b ON tx.block_id = b.id "
                    "WHERE ts.value_balance < 0 AND r.value > 0 AND b.block_hash = %s",
                    str(block_hash)
                )
            except Exception as e:
                await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                raise

    async def get_block_from_transaction_id(self, transaction_id: TransactionID | str) -> Block | None:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT b.* FROM block b JOIN transaction t ON b.id = t.block_id WHERE t.transaction_id = %s",
                        (str(transaction_id),)
                    )
                    block = await cur.fetchone()
                    if block is None:
                        return None
                    return await self._get_full_block(block, conn)
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_block_from_transition_id(self, transition_id: TransitionID | str) -> Block | None:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT tx.transaction_id FROM transaction tx "
                        "JOIN transaction_execute te ON tx.id = te.transaction_id "
                        "JOIN transition ts ON te.id = ts.transaction_execute_id "
                        "WHERE ts.transition_id = %s",
                        str(transition_id)
                    )
                    transaction_id = await cur.fetchone()
                    if transaction_id is None:
                        await cur.execute(
                            "SELECT tx.transaction_id FROM transaction tx "
                            "JOIN fee ON tx.id = fee.transaction_id "
                            "JOIN transition ts ON fee.id = ts.fee_id "
                            "WHERE ts.transition_id = %s",
                            str(transition_id)
                        )
                        transaction_id = await cur.fetchone()
                    if transaction_id is None:
                        return None
                    return await self.get_block_from_transaction_id(transaction_id['transaction_id'])
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def search_block_hash(self, block_hash: str) -> [str]:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT block_hash FROM block WHERE block_hash LIKE %s", f"{block_hash}%")
                    result = await cur.fetchall()
                    return list(map(lambda x: x['block_hash'], result))
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def search_transaction_id(self, transaction_id: str) -> [str]:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT transaction_id FROM transaction WHERE transaction_id LIKE %s", f"{transaction_id}%")
                    result = await cur.fetchall()
                    return list(map(lambda x: x['transaction_id'], result))
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def search_transition_id(self, transition_id: str) -> [str]:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT transition_id FROM transition WHERE transition_id LIKE %s", f"{transition_id}%")
                    result = await cur.fetchall()
                    return list(map(lambda x: x['transition_id'], result))
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_blocks_range(self, start, end):
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            try:
                return await Database._get_full_block_range(start, end, conn)
            except Exception as e:
                await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                raise

    async def get_blocks_range_fast(self, start, end):
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            try:
                return await Database._get_fast_block_range(start, end, conn)
            except Exception as e:
                await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                raise

    async def get_block_coinbase_reward_by_height(self, height: int) -> int | None:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT coinbase_reward FROM block WHERE height = %s", (height,)
                    )
                    # Copilot: use double quotes for strings
                    return (await cur.fetchone())["coinbase_reward"]
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_block_target_sum_by_height(self, height: int) -> int | None:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT target_sum FROM coinbase_solution "
                        "JOIN block b on coinbase_solution.block_id = b.id "
                        "WHERE height = %s ",
                        (height,)
                    )
                    return (await cur.fetchone())["target_sum"]
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_leaderboard_size(self) -> int:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT COUNT(*) FROM leaderboard")
                    return (await cur.fetchone())["count"]
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_leaderboard(self, start: int, end: int) -> list:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT * FROM leaderboard "
                        "ORDER BY total_incentive DESC, total_reward DESC "
                        "LIMIT %s OFFSET %s",
                        (end - start, start)
                    )
                    return await cur.fetchall()
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_leaderboard_rewards_by_address(self, address: str) -> (int, int):
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT total_reward, total_incentive FROM leaderboard WHERE address = %s", (address,)
                    )
                    row = await cur.fetchone()
                    return row["total_reward"], row["total_incentive"]
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_recent_solutions_by_address(self, address: str) -> list:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT b.height, b.timestamp, ps.nonce, ps.target, reward, cs.target_sum "
                        "FROM partial_solution ps "
                        "JOIN coinbase_solution cs ON cs.id = ps.coinbase_solution_id "
                        "JOIN block b ON b.id = cs.block_id "
                        "WHERE ps.address = %s "
                        "ORDER BY cs.id DESC "
                        "LIMIT 30",
                        (address,)
                    )
                    return await cur.fetchall()
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_solution_count_by_address(self, address: str) -> int:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT COUNT(*) FROM partial_solution WHERE address = %s", (address,)
                    )
                    return (await cur.fetchone())["count"]
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_solution_by_address(self, address: str, start: int, end: int) -> list:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT b.height, b.timestamp, ps.nonce, ps.target, reward, cs.target_sum "
                        "FROM partial_solution ps "
                        "JOIN coinbase_solution cs ON cs.id = ps.coinbase_solution_id "
                        "JOIN block b ON b.id = cs.block_id "
                        "WHERE ps.address = %s "
                        "ORDER BY cs.id DESC "
                        "LIMIT %s OFFSET %s",
                        (address, end - start, start)
                    )
                    return await cur.fetchall()
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_solution_by_height(self, height: int, start: int, end: int) -> list:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT ps.address, ps.nonce, ps.commitment, ps.target, reward "
                        "FROM partial_solution ps "
                        "JOIN coinbase_solution cs on ps.coinbase_solution_id = cs.id "
                        "JOIN block b on cs.block_id = b.id "
                        "WHERE b.height = %s "
                        "ORDER BY target DESC "
                        "LIMIT %s OFFSET %s",
                        (height, end - start, start)
                    )
                    return await cur.fetchall()
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def search_address(self, address: str) -> [str]:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT address FROM leaderboard WHERE address LIKE %s", f"{address}%"
                    )
                    return list(map(lambda x: x['address'], await cur.fetchall()))
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_address_speed(self, address: str) -> (float, int): # (speed, interval)
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                interval_list = [900, 1800, 3600, 14400, 43200, 86400]
                now = int(time.time())
                try:
                    for interval in interval_list:
                        await cur.execute(
                            "SELECT b.height FROM partial_solution ps "
                            "JOIN coinbase_solution cs ON ps.coinbase_solution_id = cs.id "
                            "JOIN block b ON cs.block_id = b.id "
                            "WHERE address = %s AND timestamp > %s",
                            (address, now - interval)
                        )
                        partial_solutions = await cur.fetchall()
                        if len(partial_solutions) < 10:
                            continue
                        heights = list(map(lambda x: x['height'], partial_solutions))
                        ref_heights = list(map(lambda x: x - 1, set(heights)))
                        await cur.execute(
                            "SELECT height, proof_target FROM block WHERE height = ANY(%s::bigint[])", ref_heights
                        )
                        ref_proof_targets = await cur.fetchall()
                        ref_proof_target_dict = dict(map(lambda x: (x['height'], x['proof_target']), ref_proof_targets))
                        total_solutions = 0
                        for height in heights:
                            total_solutions += ref_proof_target_dict[height - 1]
                        return total_solutions / interval, interval
                    return 0, 0
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_network_speed(self) -> float:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                now = int(time.time())
                interval = 900
                try:
                    await cur.execute(
                        "SELECT b.height FROM partial_solution ps "
                        "JOIN coinbase_solution cs ON ps.coinbase_solution_id = cs.id "
                        "JOIN block b ON cs.block_id = b.id "
                        "WHERE timestamp > %s",
                        (now - interval,)
                    )
                    partial_solutions = await cur.fetchall()
                    heights = list(map(lambda x: x['height'], partial_solutions))
                    ref_heights = list(map(lambda x: x - 1, set(heights)))
                    await cur.execute(
                        "SELECT height, proof_target FROM block WHERE height = ANY(%s::bigint[])", (ref_heights,)
                    )
                    ref_proof_targets = await cur.fetchall()
                    ref_proof_target_dict = dict(map(lambda x: (x['height'], x['proof_target']), ref_proof_targets))
                    total_solutions = 0
                    for height in heights:
                        total_solutions += ref_proof_target_dict[height - 1]
                    return total_solutions / interval
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_leaderboard_total(self) -> int:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT total_credit FROM leaderboard_total")
                    total_credit = await cur.fetchone()
                    if total_credit is None:
                        await cur.execute("INSERT INTO leaderboard_total (total_credit) VALUES (0)")
                        total_credit = 0
                    return int(total_credit["total_credit"])
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_puzzle_commitment(self, commitment: str) -> dict | None:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT reward, height FROM partial_solution "
                        "JOIN coinbase_solution cs on cs.id = partial_solution.coinbase_solution_id "
                        "JOIN block b on b.id = cs.block_id "
                        "WHERE commitment = %s",
                        (commitment,)
                    )
                    row = await cur.fetchone()
                    if row is None:
                        return None
                    return {
                        'reward': row['reward'],
                        'height': row['height']
                    }
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_function_definition(self, program_id: str, function_name: str) -> dict | None:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT * FROM program_function "
                        "JOIN program ON program.id = program_function.program_id "
                        "WHERE program.program_id = %s AND name = %s",
                        (program_id, function_name)
                    )
                    row = await cur.fetchone()
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_program_count(self, no_helloworld: bool = False) -> int:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    if no_helloworld:
                        await cur.execute(
                            "SELECT COUNT(*) FROM program "
                            "WHERE feature_hash NOT IN (SELECT hash FROM program_filter_hash)"
                        )
                    else:
                        await cur.execute("SELECT COUNT(*) FROM program")
                    return (await cur.fetchone())['count']
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_programs(self, start, end, no_helloworld: bool = False) -> list:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    where = "WHERE feature_hash NOT IN (SELECT hash FROM program_filter_hash) " if no_helloworld else ""
                    await cur.execute(
                        "SELECT p.program_id, b.height, t.transaction_id, SUM(pf.called) as called "
                        "FROM program p "
                        "JOIN transaction_deploy td on p.transaction_deploy_id = td.id "
                        "JOIN transaction t on td.transaction_id = t.id "
                        "JOIN block b on t.block_id = b.id "
                        "JOIN program_function pf on p.id = pf.program_id "
                        f"{where}"
                        "GROUP BY p.program_id, b.height, t.transaction_id "
                        "ORDER BY b.height DESC "
                        "LIMIT %s OFFSET %s",
                        (end - start, start)
                    )
                    return await cur.fetchall()
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))

    async def get_programs_with_feature_hash(self, feature_hash: bytes, start, end) -> list:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT p.program_id, b.height, t.transaction_id, SUM(pf.called) as called "
                        "FROM program p "
                        "JOIN transaction_deploy td on p.transaction_deploy_id = td.id "
                        "JOIN transaction t on td.transaction_id = t.id "
                        "JOIN block b on t.block_id = b.id "
                        "JOIN program_function pf on p.id = pf.program_id "
                        "WHERE feature_hash = %s "
                        "GROUP BY p.program_id, b.height, t.transaction_id "
                        "ORDER BY b.height "
                        "LIMIT %s OFFSET %s",
                        (feature_hash, end - start, start)
                    )
                    return await cur.fetchall()
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise


    async def get_block_by_program_id(self, program_id: str) -> Block | None:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT height FROM transaction tx "
                        "JOIN transaction_deploy td on tx.id = td.transaction_id "
                        "JOIN program p on td.id = p.transaction_deploy_id "
                        "JOIN block b on tx.block_id = b.id "
                        "WHERE p.program_id = %s",
                        (program_id,)
                    )
                    height = (await cur.fetchone())['height']
                    return await self.get_block_by_height(height)
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise


    async def get_program_called_times(self, program_id: str) -> int:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT sum(called) FROM program_function "
                        "JOIN program ON program.id = program_function.program_id "
                        "WHERE program.program_id = %s",
                        (program_id,)
                    )
                    return (await cur.fetchone())['sum']
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise


    async def get_program_calls(self, program_id: str, start: int, end: int) -> list:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT b.height, b.timestamp, ts.transition_id, function_name "
                        "FROM transition ts "
                        "JOIN transaction_execute te on te.id = ts.transaction_execute_id "
                        "JOIN transaction t on te.transaction_id = t.id "
                        "JOIN block b on t.block_id = b.id "
                        "WHERE ts.program_id = %s "
                        "ORDER BY b.height DESC "
                        "LIMIT %s OFFSET %s",
                        (program_id, end - start, start)
                    )
                    return await cur.fetchall()
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_program_similar_count(self, program_id: str) -> int:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT COUNT(*) FROM program "
                        "WHERE feature_hash = (SELECT feature_hash FROM program WHERE program_id = %s)",
                        (program_id,)
                    )
                    return (await cur.fetchone())['count'] - 1
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def get_program_feature_hash(self, program_id: str) -> bytes:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT feature_hash FROM program WHERE program_id = %s",
                        (program_id,)
                    )
                    return (await cur.fetchone())['feature_hash']
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    async def search_program(self, program_id: str) -> [str]:
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "SELECT program_id FROM program WHERE program_id LIKE %s", (f"{program_id}%",)
                    )
                    return list(map(lambda x: x['program_id'], await cur.fetchall()))
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise


    # migration methods
    async def migrate(self):
        migrations = []
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    for migrated_id, method in migrations:
                        await cur.execute("SELECT COUNT(*) FROM _migration WHERE migrated_id = %s", (migrated_id,))
                        if (await cur.fetchone())['count'] == 0:
                            print(f"DB migrating {migrated_id}")
                            async with conn.transaction():
                                await method()
                                await cur.execute("INSERT INTO _migration (migrated_id) VALUES (%s)", (migrated_id,))
                except Exception as e:
                    await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                    raise

    # debug method
    async def clear_database(self):
        conn: psycopg.AsyncConnection
        async with self.pool.connection() as conn:
            try:
                await conn.execute("TRUNCATE TABLE block RESTART IDENTITY CASCADE")
            except Exception as e:
                await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseError, e))
                raise