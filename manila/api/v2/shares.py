# Copyright (c) 2015 Mirantis inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import six
import webob
from webob import exc

from manila.api.openstack import api_version_request as api_version
from manila.api.openstack import wsgi
from manila.api.v1 import share_manage
from manila.api.v1 import share_unmanage
from manila.api.v1 import shares
from manila.api.views import share_accesses as share_access_views
from manila.api.views import share_migration as share_migration_views
from manila.api.views import shares as share_views
from manila import db
from manila import exception
from manila.i18n import _
from manila import share
from manila import utils


class ShareController(shares.ShareMixin,
                      share_manage.ShareManageMixin,
                      share_unmanage.ShareUnmanageMixin,
                      wsgi.Controller,
                      wsgi.AdminActionsMixin):
    """The Shares API v2 controller for the OpenStack API."""
    resource_name = 'share'
    _view_builder_class = share_views.ViewBuilder

    def __init__(self):
        super(self.__class__, self).__init__()
        self.share_api = share.API()
        self._access_view_builder = share_access_views.ViewBuilder()
        self._migration_view_builder = share_migration_views.ViewBuilder()

    @wsgi.Controller.api_version("2.0", "2.3")
    def create(self, req, body):
        # Remove consistency group attributes
        body.get('share', {}).pop('consistency_group_id', None)
        share = self._create(req, body)
        return share

    @wsgi.Controller.api_version("2.4", "2.23")  # noqa
    def create(self, req, body):  # pylint: disable=E0102
        return self._create(req, body)

    @wsgi.Controller.api_version("2.24")  # noqa
    def create(self, req, body):  # pylint: disable=E0102
        return self._create(req, body,
                            check_create_share_from_snapshot_support=True)

    @wsgi.Controller.api_version('2.0', '2.6')
    @wsgi.action('os-reset_status')
    def share_reset_status_legacy(self, req, id, body):
        return self._reset_status(req, id, body)

    @wsgi.Controller.api_version('2.7')
    @wsgi.action('reset_status')
    def share_reset_status(self, req, id, body):
        return self._reset_status(req, id, body)

    @wsgi.Controller.api_version('2.0', '2.6')
    @wsgi.action('os-force_delete')
    def share_force_delete_legacy(self, req, id, body):
        return self._force_delete(req, id, body)

    @wsgi.Controller.api_version('2.7')
    @wsgi.action('force_delete')
    def share_force_delete(self, req, id, body):
        return self._force_delete(req, id, body)

    @wsgi.Controller.api_version('2.22', experimental=True)
    @wsgi.action("migration_start")
    @wsgi.Controller.authorize
    def migration_start(self, req, id, body):
        """Migrate a share to the specified host."""
        context = req.environ['manila.context']
        try:
            share = self.share_api.get(context, id)
        except exception.NotFound:
            msg = _("Share %s not found.") % id
            raise exc.HTTPNotFound(explanation=msg)
        params = body.get('migration_start')

        if not params:
            raise exc.HTTPBadRequest(explanation=_("Request is missing body."))

        try:
            host = params['host']
        except KeyError:
            raise exc.HTTPBadRequest(explanation=_("Must specify 'host'."))

        force_host_assisted_migration = utils.get_bool_from_api_params(
            'force_host_assisted_migration', params)

        new_share_network = None
        new_share_type = None

        preserve_metadata = utils.get_bool_from_api_params('preserve_metadata',
                                                           params, True)
        writable = utils.get_bool_from_api_params('writable', params, True)
        nondisruptive = utils.get_bool_from_api_params('nondisruptive', params)

        new_share_network_id = params.get('new_share_network_id', None)
        if new_share_network_id:
            try:
                new_share_network = db.share_network_get(
                    context, new_share_network_id)
            except exception.NotFound:
                msg = _("Share network %s not "
                        "found.") % new_share_network_id
                raise exc.HTTPBadRequest(explanation=msg)

        new_share_type_id = params.get('new_share_type_id', None)
        if new_share_type_id:
            try:
                new_share_type = db.share_type_get(
                    context, new_share_type_id)
            except exception.NotFound:
                msg = _("Share type %s not found.") % new_share_type_id
                raise exc.HTTPBadRequest(explanation=msg)

        try:
            self.share_api.migration_start(
                context, share, host, force_host_assisted_migration,
                preserve_metadata, writable, nondisruptive,
                new_share_network=new_share_network,
                new_share_type=new_share_type)
        except exception.Conflict as e:
            raise exc.HTTPConflict(explanation=six.text_type(e))

        return webob.Response(status_int=202)

    @wsgi.Controller.api_version('2.22', experimental=True)
    @wsgi.action("migration_complete")
    @wsgi.Controller.authorize
    def migration_complete(self, req, id, body):
        """Invokes 2nd phase of share migration."""
        context = req.environ['manila.context']
        try:
            share = self.share_api.get(context, id)
        except exception.NotFound:
            msg = _("Share %s not found.") % id
            raise exc.HTTPNotFound(explanation=msg)
        self.share_api.migration_complete(context, share)
        return webob.Response(status_int=202)

    @wsgi.Controller.api_version('2.22', experimental=True)
    @wsgi.action("migration_cancel")
    @wsgi.Controller.authorize
    def migration_cancel(self, req, id, body):
        """Attempts to cancel share migration."""
        context = req.environ['manila.context']
        try:
            share = self.share_api.get(context, id)
        except exception.NotFound:
            msg = _("Share %s not found.") % id
            raise exc.HTTPNotFound(explanation=msg)
        self.share_api.migration_cancel(context, share)
        return webob.Response(status_int=202)

    @wsgi.Controller.api_version('2.22', experimental=True)
    @wsgi.action("migration_get_progress")
    @wsgi.Controller.authorize
    def migration_get_progress(self, req, id, body):
        """Retrieve share migration progress for a given share."""
        context = req.environ['manila.context']
        try:
            share = self.share_api.get(context, id)
        except exception.NotFound:
            msg = _("Share %s not found.") % id
            raise exc.HTTPNotFound(explanation=msg)
        result = self.share_api.migration_get_progress(context, share)

        # refresh share model
        share = self.share_api.get(context, id)

        return self._migration_view_builder.get_progress(req, share, result)

    @wsgi.Controller.api_version('2.22', experimental=True)
    @wsgi.action("reset_task_state")
    @wsgi.Controller.authorize
    def reset_task_state(self, req, id, body):
        return self._reset_status(req, id, body, status_attr='task_state')

    @wsgi.Controller.api_version('2.0', '2.6')
    @wsgi.action('os-allow_access')
    def allow_access_legacy(self, req, id, body):
        """Add share access rule."""
        return self._allow_access(req, id, body)

    @wsgi.Controller.api_version('2.7')
    @wsgi.action('allow_access')
    def allow_access(self, req, id, body):
        """Add share access rule."""
        if req.api_version_request < api_version.APIVersionRequest("2.13"):
            return self._allow_access(req, id, body)
        else:
            return self._allow_access(req, id, body, enable_ceph=True)

    @wsgi.Controller.api_version('2.0', '2.6')
    @wsgi.action('os-deny_access')
    def deny_access_legacy(self, req, id, body):
        """Remove share access rule."""
        return self._deny_access(req, id, body)

    @wsgi.Controller.api_version('2.7')
    @wsgi.action('deny_access')
    def deny_access(self, req, id, body):
        """Remove share access rule."""
        return self._deny_access(req, id, body)

    @wsgi.Controller.api_version('2.0', '2.6')
    @wsgi.action('os-access_list')
    def access_list_legacy(self, req, id, body):
        """List share access rules."""
        return self._access_list(req, id, body)

    @wsgi.Controller.api_version('2.7')
    @wsgi.action('access_list')
    def access_list(self, req, id, body):
        """List share access rules."""
        return self._access_list(req, id, body)

    @wsgi.Controller.api_version('2.0', '2.6')
    @wsgi.action('os-extend')
    def extend_legacy(self, req, id, body):
        """Extend size of a share."""
        return self._extend(req, id, body)

    @wsgi.Controller.api_version('2.7')
    @wsgi.action('extend')
    def extend(self, req, id, body):
        """Extend size of a share."""
        return self._extend(req, id, body)

    @wsgi.Controller.api_version('2.0', '2.6')
    @wsgi.action('os-shrink')
    def shrink_legacy(self, req, id, body):
        """Shrink size of a share."""
        return self._shrink(req, id, body)

    @wsgi.Controller.api_version('2.7')
    @wsgi.action('shrink')
    def shrink(self, req, id, body):
        """Shrink size of a share."""
        return self._shrink(req, id, body)

    @wsgi.Controller.api_version('2.7', '2.7')
    def manage(self, req, body):
        body.get('share', {}).pop('is_public', None)
        detail = self._manage(req, body)
        return detail

    @wsgi.Controller.api_version("2.8")  # noqa
    def manage(self, req, body):  # pylint: disable=E0102
        detail = self._manage(req, body)
        return detail

    @wsgi.Controller.api_version('2.7')
    @wsgi.action('unmanage')
    def unmanage(self, req, id, body=None):
        return self._unmanage(req, id, body)


def create_resource():
    return wsgi.Resource(ShareController())
