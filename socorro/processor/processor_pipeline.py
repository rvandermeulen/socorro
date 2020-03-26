# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""this file defines the method of converting a raw crash into a processed
crash.  In this latest version, all transformations have been reimplemented
as sets of loadable rules.  The rules are applied one at a time, each doing
some small part of the transformation process."""

import logging
import os
import tempfile

from configman import Namespace, RequiredConfig
from configman.converters import str_to_list
from configman.dotdict import DotDict

from socorro.lib import sentry_client
from socorro.lib.datetimeutil import utc_now
from socorro.processor.rules.breakpad import (
    BreakpadStackwalkerRule2015,
    CrashingThreadRule,
    JitCrashCategorizeRule,
    MinidumpSha256Rule,
)
from socorro.processor.rules.general import (
    CPUInfoRule,
    DeNoneRule,
    DeNullRule,
    IdentifierRule,
    OSInfoRule,
)
from socorro.processor.rules.memory_report_extraction import MemoryReportExtraction
from socorro.processor.rules.mozilla import (
    AddonsRule,
    BetaVersionRule,
    ConvertModuleSignatureInfoRule,
    DatesAndTimesRule,
    EnvironmentRule,
    ESRVersionRewrite,
    ExploitablityRule,
    FenixVersionRewriteRule,
    FlashVersionRule,
    JavaProcessRule,
    ModulesInStackRule,
    MozCrashReasonRule,
    OSPrettyVersionRule,
    OutOfMemoryBinaryRule,
    PHCRule,
    PluginContentURL,
    PluginRule,
    PluginUserComment,
    ProductRewrite,
    ProductRule,
    SignatureGeneratorRule,
    ThemePrettyNameRule,
    TopMostFilesRule,
    UserDataRule,
)


class ProcessorPipeline(RequiredConfig):
    """Processor pipeline for Mozilla crash ingestion."""

    required_config = Namespace("transform_rules")

    # BreakpadStackwalkerRule2015 configuration
    required_config.breakpad = Namespace()
    required_config.breakpad.add_option(
        "dump_field", doc="the default name of a dump", default="upload_file_minidump"
    )
    required_config.breakpad.add_option(
        "command_pathname",
        doc="the full pathname to the external program to run (quote path with embedded spaces)",
        default="/stackwalk/stackwalker",
    )
    required_config.breakpad.add_option(
        name="symbols_urls",
        doc="comma-delimited ordered list of urls for symbol lookup",
        default="https://localhost",
        from_string_converter=str_to_list,
        likely_to_be_changed=True,
    )
    required_config.breakpad.add_option(
        "command_line",
        doc="template for the command to invoke the external program; uses Python format syntax",
        default=(
            "timeout --signal KILL {kill_timeout} {command_pathname} "
            "--raw-json {raw_crash_pathname} "
            "{symbols_urls} "
            "--symbols-cache {symbol_cache_path} "
            "--symbols-tmp {symbol_tmp_path} "
            "{dump_file_pathname}"
        ),
    )
    required_config.breakpad.add_option(
        "kill_timeout",
        doc="amount of time in seconds to let mdsw run before declaring it hung",
        default=600,
    )
    required_config.breakpad.add_option(
        "symbol_tmp_path",
        doc=(
            "directory to use as temp space for downloading symbols--must be "
            "on the same filesystem as symbols-cache"
        ),
        default=os.path.join(tempfile.gettempdir(), "symbols-tmp"),
    ),
    required_config.breakpad.add_option(
        "symbol_cache_path",
        doc=(
            "the path where the symbol cache is found, this location must be "
            "readable and writeable (quote path with embedded spaces)"
        ),
        default=os.path.join(tempfile.gettempdir(), "symbols"),
    )
    required_config.breakpad.add_option(
        "tmp_storage_path",
        doc="a path where temporary files may be written",
        default=tempfile.gettempdir(),
    )

    # JitClassCategorizationRule configuration
    required_config.jit = Namespace()
    required_config.jit.add_option(
        "kill_timeout",
        doc="amount of time to let command run before declaring it hung",
        default=600,
    )
    required_config.jit.add_option(
        "dump_field", doc="the default name of a dump", default="upload_file_minidump"
    )
    required_config.jit.add_option(
        "command_pathname",
        doc="full pathname to external program; quote path with embedded spaces",
        default="/stackwalk/jit-crash-categorize",
    )
    required_config.jit.add_option(
        "command_line",
        doc="template for command line; uses Python format syntax",
        default=(
            "timeout -s KILL {kill_timeout} {command_pathname} {dump_file_pathname}"
        ),
    )

    # BetaVersionRule configuration
    required_config.betaversion = Namespace()
    required_config.betaversion.add_option(
        "version_string_api",
        doc="url for the version string api endpoint in the webapp",
        default="https://crash-stats.mozilla.org/api/VersionString",
    )

    def __init__(self, config, rules=None):
        super().__init__()
        self.config = config
        self.logger = logging.getLogger(__name__ + "." + self.__class__.__name__)

        self.rules = rules or self.get_ruleset(config)
        for rule in self.rules:
            self.logger.info("Loaded rule: %r" % rule)

    def get_ruleset(self, config):
        """Generate rule set for Mozilla crash processing.

        :arg config: configman DotDict config instance

        :returns: pipeline of rules

        """
        return [
            # fix the raw crash removing null characters and Nones
            DeNullRule(),
            DeNoneRule(),
            # fix ModuleSignatureInfo if it needs fixing
            ConvertModuleSignatureInfoRule(),
            # rules to change the internals of the raw crash
            ProductRewrite(),
            FenixVersionRewriteRule(),
            ESRVersionRewrite(),
            PluginContentURL(),
            PluginUserComment(),
            # rules to transform a raw crash into a processed crash
            IdentifierRule(),
            MinidumpSha256Rule(),
            BreakpadStackwalkerRule2015(
                dump_field=config.breakpad.dump_field,
                symbols_urls=config.breakpad.symbols_urls,
                command_line=config.breakpad.command_line,
                command_pathname=config.breakpad.command_pathname,
                kill_timeout=config.breakpad.kill_timeout,
                symbol_tmp_path=config.breakpad.symbol_tmp_path,
                symbol_cache_path=config.breakpad.symbol_cache_path,
                tmp_storage_path=config.breakpad.tmp_storage_path,
            ),
            ProductRule(),
            UserDataRule(),
            EnvironmentRule(),
            PluginRule(),
            AddonsRule(),
            DatesAndTimesRule(),
            OutOfMemoryBinaryRule(),
            PHCRule(),
            JavaProcessRule(),
            MozCrashReasonRule(),
            # post processing of the processed crash
            CrashingThreadRule(),
            CPUInfoRule(),
            OSInfoRule(),
            BetaVersionRule(version_string_api=config.betaversion.version_string_api),
            ExploitablityRule(),
            FlashVersionRule(),
            OSPrettyVersionRule(),
            TopMostFilesRule(),
            ModulesInStackRule(),
            ThemePrettyNameRule(),
            MemoryReportExtraction(),
            # generate signature now that we've done all the processing it depends on
            SignatureGeneratorRule(),
            # a set of classifiers to help with jit crashes--must be last since it
            # depends on signature generation
            JitCrashCategorizeRule(
                dump_field=config.jit.dump_field,
                command_line=config.jit.command_line,
                command_pathname=config.jit.command_pathname,
                kill_timeout=config.jit.kill_timeout,
            ),
        ]

    def process_crash(self, raw_crash, raw_dumps, processed_crash):
        """Take a raw_crash and its associated raw_dumps and return a processed_crash

        If this throws an exception, the crash was not processed correctly.

        """
        # processor_meta_data will be used to ferry "inside information" to
        # transformation rules. Sometimes rules need a bit more extra
        # information about the transformation process itself.
        processor_meta_data = DotDict()
        processor_meta_data.processor_notes = [
            self.config.processor_name,
            self.__class__.__name__,
        ]
        processor_meta_data.processor = self
        processor_meta_data.config = self.config

        if "processor_notes" in processed_crash:
            original_processor_notes = [
                x.strip() for x in processed_crash.processor_notes.split(";")
            ]
            processor_meta_data.processor_notes.append(
                "earlier processing: %s"
                % processed_crash.get("started_datetime", "Unknown Date")
            )
        else:
            original_processor_notes = []

        processed_crash.success = False
        processed_crash.started_datetime = utc_now()
        # for backwards compatibility:
        processed_crash.startedDateTime = processed_crash.started_datetime
        processed_crash.signature = "EMPTY: crash failed to process"

        crash_id = raw_crash["uuid"]

        start_time = self.logger.info("starting transform for crash: %s", crash_id)
        processor_meta_data.started_timestamp = start_time

        # Apply rules; if a rule fails, capture the error and continue onward
        for rule in self.rules:
            try:
                rule.act(raw_crash, raw_dumps, processed_crash, processor_meta_data)

            except Exception as exc:
                # If a rule throws an error, capture it and toss it in the
                # processor notes
                sentry_client.capture_error(
                    logger=self.logger, extra={"crash_id": crash_id}
                )
                # NOTE(willkg): notes are public, so we can't put exception
                # messages in them
                processor_meta_data.processor_notes.append(
                    "rule %s failed: %s"
                    % (rule.__class__.__name__, exc.__class__.__name__)
                )

        # The crash made it through the processor rules with no exceptions
        # raised, call it a success
        processed_crash.success = True

        # The processor notes are in the form of a list.  Join them all
        # together to make a single string
        processor_meta_data.processor_notes.extend(original_processor_notes)
        processed_crash.processor_notes = "; ".join(processor_meta_data.processor_notes)
        completed_datetime = utc_now()
        processed_crash.completed_datetime = completed_datetime

        # For backwards compatibility
        processed_crash.completeddatetime = completed_datetime

        self.logger.info(
            "finishing %s transform for crash: %s",
            "successful" if processed_crash.success else "failed",
            crash_id,
        )
        return processed_crash

    def reject_raw_crash(self, crash_id, reason):
        self.logger.warning("%s rejected: %s", crash_id, reason)

    def close(self):
        self.logger.debug("closing rules")
        for rule in self.rules:
            rule.close()
