#!/usr/bin/env python3
# Copyright (c) 2022 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test the sweep RPC command."""

from decimal import Decimal, getcontext

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import (
    assert_equal,
    assert_greater_than,
    assert_raises_rpc_error,
)

# Decorator to reset sweepwallet to zero utxos
def cleanup(func):
    def wrapper(self):
        try:
            func(self)
        finally:
            if 0 < self.wallet.getbalances()["mine"]["trusted"]:
                self.wallet.sweepwallet([self.sweep_target])
            assert_equal(0, self.wallet.getbalances()["mine"]["trusted"]) # wallet is empty
    return wrapper

class SweepwalletTest(BitcoinTestFramework):
# Setup and helpers
    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()

    def set_test_params(self):
        getcontext().prec=10
        self.num_nodes = 1
        self.setup_clean_chain = True

    def assert_balance_swept_completely(self, tx, balance):
        output_sum = sum([o["value"] for o in tx["decoded"]["vout"]])
        assert_equal(output_sum, balance + tx["fee"])
        assert_equal(0, self.wallet.getbalances()["mine"]["trusted"]) # wallet is empty

    def assert_tx_has_output(self, tx, addr, value=None ):
        for output in tx["decoded"]["vout"]:
            if addr == output["scriptPubKey"]["address"] and value is None or value == output["value"]:
                return
        raise AssertionError("Output to {} not present or wrong amount".format(addr))

    def assert_tx_has_outputs(self, tx, expected_outputs):
        assert_equal(len(expected_outputs), len(tx["decoded"]["vout"]))
        for eo in expected_outputs:
            # TODO: this would match the same output twice if expected_output specified two equivalent outputs
            self.assert_tx_has_output(tx, eo["address"], eo["value"])

    def add_uxtos(self, amounts):
        for a in amounts:
            self.def_wallet.sendtoaddress(self.wallet.getnewaddress(), a)
        self.generate(self.nodes[0], 1)
        assert_greater_than(self.wallet.getbalances()["mine"]["trusted"], 0)
        return self.wallet.getbalances()["mine"]["trusted"]

    @cleanup
    def gen_and_clean(self):
        self.add_uxtos([15, 2, 4])

    def test_cleanup(self):
        self.log.info("Test that cleanup wrapper empties wallet")
        self.gen_and_clean()
        assert_equal(0, self.wallet.getbalances()["mine"]["trusted"]) # wallet is empty

    # Helper schema for success cases
    def test_sweepwallet_success(self, sweepwallet_args, remaining_balance = 0):
        sweep_tx_receipt = self.wallet.sweepwallet(sweepwallet_args)
        self.generate(self.nodes[0], 1)
        # wallet has remaining balance (usually empty
        assert_equal(remaining_balance, self.wallet.getbalances()["mine"]["trusted"])

        assert_equal(sweep_tx_receipt["complete"], True)
        return self.wallet.gettransaction(txid = sweep_tx_receipt["txid"], verbose = True)

# Actual tests
    @cleanup
    def sweepwallet_two_utxos(self):
        self.log.info("Testing basic sweep case without specific amounts")
        initial_balance = self.add_uxtos([10,11])
        tx_from_wallet = self.test_sweepwallet_success(sweepwallet_args = [self.sweep_target])

        self.assert_tx_has_outputs(tx = tx_from_wallet,
            expected_outputs = [
                { "address": self.sweep_target, "value": initial_balance + tx_from_wallet["fee"] } # fee is neg
            ]
        )

    @cleanup
    def sweepwallet_split(self):
        self.log.info("Testing sweep where two recipients have unspecified amount")
        wallet_balance_before_sweep = self.add_uxtos([1, 2, 3, 15])
        tx_from_wallet = self.test_sweepwallet_success([self.sweep_target, self.split_target])

        half = (wallet_balance_before_sweep + tx_from_wallet["fee"]) / 2
        self.assert_tx_has_outputs(tx_from_wallet,
            expected_outputs = [
                { "address": self.split_target, "value": half },
                { "address": self.sweep_target, "value": half }
            ]
        )
        self.assert_balance_swept_completely(tx_from_wallet, wallet_balance_before_sweep)

    @cleanup
    def sweepwallet_and_spend(self):
        self.log.info("Testing sweep where one output has specified amount")
        wallet_balance_before_sweep = self.add_uxtos([8, 13])
        tx_from_wallet = self.test_sweepwallet_success([{self.recipient: 5}, self.sweep_target])

        self.assert_tx_has_outputs(tx_from_wallet,
            expected_outputs = [
                { "address": self.recipient, "value": 5 },
                { "address": self.sweep_target, "value": wallet_balance_before_sweep - 5 + tx_from_wallet["fee"] }
            ]
        )
        self.assert_balance_swept_completely(tx_from_wallet, wallet_balance_before_sweep)

    @cleanup
    def sweepwallet_invalid_receiver_addresses(self):
        self.log.info("Testing sweep only with specified amount")
        self.add_uxtos([12, 9])

        assert_raises_rpc_error(
                -8,
                "Must provide at least one address without a specified amount" ,
                self.wallet.sweepwallet,
                [{self.recipient: 5}]
            )

    @cleanup
    def sweepwallet_invalid_amounts(self):
        self.log.info("Try sweeping more than balance")
        wallet_balance_before_sweep = self.add_uxtos([7, 14])

        expected_tx = self.wallet.sweepwallet(receivers=[{self.recipient: 5}, self.sweep_target], options={"add_to_wallet": False})
        tx = self.wallet.decoderawtransaction(expected_tx['hex'])
        fee = 21 - sum([o["value"] for o in tx["vout"]])

        assert_raises_rpc_error(-8, "Assigned more value to outputs than available funds.", self.wallet.sweepwallet,
                [ {self.recipient: wallet_balance_before_sweep + 1}, self.sweep_target ])
        assert_raises_rpc_error(-6, "Insufficient funds for fees after creating specified outputs.", self.wallet.sweepwallet,
                [{self.recipient: wallet_balance_before_sweep}, self.sweep_target])
        assert_raises_rpc_error(-8, "Specified output amount to {} is below dust threshold".format(self.recipient),
                self.wallet.sweepwallet, [{self.recipient: 0.00000001}, self.sweep_target])
        assert_raises_rpc_error(-6, "Dynamically assigned remainder results in dust output.", self.wallet.sweepwallet,
                [{self.recipient: wallet_balance_before_sweep - fee}, self.sweep_target])
        assert_raises_rpc_error(-6, "Dynamically assigned remainder results in dust output.", self.wallet.sweepwallet,
                [{self.recipient: wallet_balance_before_sweep - fee - Decimal(0.00000010)}, self.sweep_target])

    def sweepwallet_negative_effective_value(self):
        self.log.info("Check that sweep fails if all UTXOs have negative effective value")
        # Use dedicated wallet for dust amounts and unload wallet at end
        self.nodes[0].createwallet("dustwallet")
        dust_wallet = self.nodes[0].get_wallet_rpc("dustwallet")

        self.def_wallet.sendtoaddress(dust_wallet.getnewaddress(), 0.00000400)
        self.def_wallet.sendtoaddress(dust_wallet.getnewaddress(), 0.00000300)
        self.generate(self.nodes[0], 1)
        assert_greater_than(dust_wallet.getbalances()["mine"]["trusted"], 0)

        assert_raises_rpc_error(-6, "Total value of UTXO pool too low to pay for sweep. Try using lower feerate or excluding uneconomic UTXOs with 'sendmax' option.", dust_wallet.sweepwallet, receivers=[self.sweep_target], fee_rate=300)

        dust_wallet.unloadwallet()

    @cleanup
    def sweepwallet_with_send_max(self):
        self.log.info("Check that `send_max` option causes negative value UTXOs to be left behind")
        self.add_uxtos([0.00000400, 0.00000300, 1])

        # Sweep with send_max
        sweep_tx_receipt = self.wallet.sweepwallet(receivers=[self.sweep_target], fee_rate=300, options={"send_max": True})
        tx_from_wallet = self.wallet.gettransaction(txid = sweep_tx_receipt["txid"], verbose = True)

        assert_equal(len(tx_from_wallet["decoded"]["vin"]), 1)
        assert_equal(len(tx_from_wallet["decoded"]["vout"]), 1)
        self.assert_tx_has_output(tx_from_wallet, self.sweep_target)
        assert_equal(self.wallet.getbalances()["mine"]["trusted"], Decimal("0.00000700"))

        self.def_wallet.sendtoaddress(self.wallet.getnewaddress(), 1)
        self.generate(self.nodes[0], 1)

    @cleanup
    def sweepwallet_specific_inputs(self):
        self.log.info("Sweep only one specified input out of multiple")
        self.add_uxtos([17, 4])
        utxo = self.wallet.listunspent()[0]

        sweep_tx_receipt = self.wallet.sweepwallet(receivers=[self.sweep_target], options={"inputs": [utxo]})
        tx_from_wallet = self.wallet.gettransaction(txid = sweep_tx_receipt["txid"], verbose = True)
        assert_equal(len(tx_from_wallet["decoded"]["vin"]), 1)
        assert_equal(len(tx_from_wallet["decoded"]["vout"]), 1)
        assert_equal(tx_from_wallet["decoded"]["vin"][0]["txid"], utxo["txid"])
        assert_equal(tx_from_wallet["decoded"]["vin"][0]["vout"], utxo["vout"])
        self.assert_tx_has_output(tx_from_wallet, self.sweep_target)

        self.generate(self.nodes[0], 1)
        assert_greater_than(self.wallet.getbalances()["mine"]["trusted"], 0)

        # Clean up remaining UTXO
        self.wallet.sweepwallet(receivers=[self.sweep_target])
        self.generate(self.nodes[0], 1)
        assert_equal(0, self.wallet.getbalances()["mine"]["trusted"]) # wallet is empty

    @cleanup
    def sweepwallet_fails_on_missing_input(self):
        # fails because UTXO was previously spent, and wallet is empty
        self.log.info("Sweep fails because specified UTXO is not available")
        self.add_uxtos([16, 5])
        spent_utxo = self.wallet.listunspent()[0]

        # fails on unconfirmed spent UTXO
        self.wallet.sweepwallet(receivers=[self.sweep_target])
        assert_raises_rpc_error(-8,
                "Input not available. UTXO ({}:{}) was already spent.".format(spent_utxo["txid"], spent_utxo["vout"]),
                self.wallet.sweepwallet, receivers=[self.sweep_target], options={"inputs": [spent_utxo]})

        # fails on specific previously spent UTXO, while other UTXOs exist
        self.generate(self.nodes[0], 1)
        self.add_uxtos([19, 2])
        assert_raises_rpc_error(-8,
                "Input not available. UTXO ({}:{}) was already spent.".format(spent_utxo["txid"], spent_utxo["vout"]),
                self.wallet.sweepwallet, receivers=[self.sweep_target], options={"inputs": [spent_utxo]})

        # fails because UTXO is unknown, while other UTXOs exist
        foreign_utxo = self.def_wallet.listunspent()[0]
        assert_raises_rpc_error(-8, "Input not found. UTXO ({}:{}) is not part of wallet.".format(foreign_utxo["txid"], foreign_utxo["vout"]), self.wallet.sweepwallet, receivers=[self.sweep_target], options={"inputs": [foreign_utxo]})

    def run_test(self):
        self.nodes[0].createwallet("sweepwallet")
        self.wallet = self.nodes[0].get_wallet_rpc("sweepwallet")
        self.def_wallet  = self.nodes[0].get_wallet_rpc(self.default_wallet_name)
        self.generate(self.nodes[0], 101)
        self.recipient = self.def_wallet.getnewaddress() # payee for a specific amount
        self.sweep_target = self.def_wallet.getnewaddress() # address that receives swept rest
        self.split_target = self.def_wallet.getnewaddress() # 2nd target when splitting rest

        # Test cleanup
        self.test_cleanup()

        # Basic sweep: everything to one address
        self.sweepwallet_two_utxos()

        # Sweep to two addresses with equal amounts
        self.sweepwallet_split()

        # Pay recipient and sweep remainder
        self.sweepwallet_and_spend()

        # Sweep fails if no receiver has unspecified amount
        self.sweepwallet_invalid_receiver_addresses()

        # Sweep fails when trying to spend more than the balance
        self.sweepwallet_invalid_amounts()

        # Sweep fails when wallet has no economically spendable UTXOs
        self.sweepwallet_negative_effective_value()

        # Leave dust behind if using send_max
        self.sweepwallet_with_send_max()

        # Sweep succeeds with specific inputs
        self.sweepwallet_specific_inputs()

        # Fails for the right reasons on missing or previously spent UTXOs
        self.sweepwallet_fails_on_missing_input()

if __name__ == '__main__':
    SweepwalletTest().main()
