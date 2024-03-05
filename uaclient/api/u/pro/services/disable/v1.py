import logging
from typing import List, Optional

from uaclient import entitlements, lock, messages, status, util
from uaclient.api import AbstractProgress, ProgressWrapper, exceptions
from uaclient.api.api import APIEndpoint
from uaclient.api.data_types import AdditionalInfo
from uaclient.api.u.pro.status.enabled_services.v1 import _enabled_services
from uaclient.api.u.pro.status.is_attached.v1 import _is_attached
from uaclient.config import UAConfig
from uaclient.data_types import (
    BoolDataValue,
    DataObject,
    Field,
    StringDataValue,
    data_list,
)

LOG = logging.getLogger(util.replace_top_level_logger_name(__name__))


class DisableOptions(DataObject):
    fields = [
        Field("service", StringDataValue),
        Field("purge", BoolDataValue, False),
    ]

    def __init__(self, *, service: str, purge: bool = False):
        self.service = service
        self.purge = purge


class DisableResult(DataObject, AdditionalInfo):
    fields = [
        Field("disabled", data_list(StringDataValue)),
    ]

    def __init__(self, *, disabled: List[str]):
        self.disabled = disabled


def _enabled_services_names(cfg: UAConfig) -> List[str]:
    return [s.name for s in _enabled_services(cfg).enabled_services]


def disable(
    options: DisableOptions, progress_object: Optional[AbstractProgress] = None
) -> DisableResult:
    return _disable(options, UAConfig(), progress_object=progress_object)


def _disable(
    options: DisableOptions,
    cfg: UAConfig,
    progress_object: Optional[AbstractProgress] = None,
) -> DisableResult:
    progress = ProgressWrapper(progress_object)

    if not util.we_are_currently_root():
        raise exceptions.NonRootUserError()

    if not _is_attached(cfg).is_attached:
        raise exceptions.UnattachedError()

    entitlement = entitlements.entitlement_factory(
        cfg=cfg,
        name=options.service,
        assume_yes=True,
        purge=options.purge,
    )

    enabled_services_before = _enabled_services_names(cfg)

    # Do this after getting the class so that the factory can raise an
    # exception for invalid service names
    if options.service not in enabled_services_before:
        # nothing to do
        return DisableResult(
            disabled=[],
        )

    variant = entitlement.enabled_variant
    if variant is not None:
        entitlement = variant

    progress.total_steps = entitlement.calculate_total_disable_steps()

    success = False
    fail_reason = None

    try:
        with lock.RetryLock(
            lock_holder="u.pro.services.disable.v1",
        ):
            success, fail_reason = entitlement.disable(progress)
    except Exception as e:
        lock.clear_lock_file_if_present()
        raise e

    if not success:
        if fail_reason is not None and fail_reason.message is not None:
            reason = fail_reason.message
        else:
            reason = messages.GENERIC_UNKNOWN_ISSUE
        raise exceptions.EntitlementNotDisabledError(
            service=options.service, reason=reason
        )

    enabled_services_after = _enabled_services_names(cfg)

    status.status(cfg=cfg)  # Update the status cache
    progress.finish()

    return DisableResult(
        disabled=sorted(
            list(
                set(enabled_services_before).difference(
                    set(enabled_services_after)
                )
            )
        ),
    )


endpoint = APIEndpoint(
    version="v1",
    name="DisableService",
    fn=_disable,
    options_cls=DisableOptions,
    supports_progress=True,
)
