import logging
from couchdbkit.exceptions import ResourceNotFound
from corehq.apps.hqwebapp.forms import BulkUploadForm
from corehq.apps.hqwebapp.tasks import send_html_email_async
from dimagi.utils.decorators.memoized import memoized
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.views import redirect_to_login
from django.core.urlresolvers import reverse
from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.translation import ugettext as _
from django.views.generic import TemplateView

from corehq.apps.domain.models import Domain
from corehq.apps.hqwebapp.views import logout
from corehq.apps.registration.forms import NewWebUserRegistrationForm
from corehq.apps.registration.utils import activate_new_user
from corehq.apps.users.models import Invitation, CouchUser, WebUser, DomainInvitation
from Crypto.PublicKey import RSA
from Crypto.Hash import SHA256
from Crypto.Signature import PKCS1_PSS

logger = logging.getLogger(__name__)


@memoized
def get_hq_private_key():
    if settings.HQ_PRIVATE_KEY:
        return RSA.importKey(settings.HQ_PRIVATE_KEY)

    raise Exception('No private key found in localsettings.HQ_PRIVATE_KEY')


def sign(message):
    """
    Signs the SHA256 hash of message with HQ's private key, and returns
    the binary signature. The scheme used is RSASSA-PSS.
    """
    private_key = get_hq_private_key()
    sha256_hash = SHA256.new(message)
    signature = PKCS1_PSS.new(private_key).sign(sha256_hash)
    return signature


def send_confirmation_email(invitation):
    invited_user = invitation.email
    subject = '%s accepted your invitation to CommCare HQ' % invited_user
    recipient = WebUser.get_by_user_id(invitation.invited_by).get_email()
    context = {
        'invited_user': invited_user,
    }
    html_content = render_to_string('domain/email/invite_confirmation.html',
                                    context)
    text_content = render_to_string('domain/email/invite_confirmation.txt',
                                    context)
    send_html_email_async.delay(subject, recipient, html_content,
                                text_content=text_content)


class InvitationView(object):
    # todo cleanup this view so it properly inherits from BaseSectionPageView
    inv_id = None
    inv_type = Invitation
    template = ""
    need = [] # a list of strings containing which parameters of the call function should be set as attributes to self

    def added_context(self):
        username = self.request.user.username
        # Add zero-width space for better line breaking
        username = username.replace("@", "&#x200b;@")
        return {
            'create_domain': False,
            'formatted_username': username,
        }

    def validate_invitation(self, invitation):
        pass

    def is_invited(self, invitation, couch_user):
        raise NotImplementedError

    @property
    def success_msg(self):
        return _("You have been successfully invited")

    @property
    def redirect_to_on_success(self):
        raise NotImplementedError

    @property
    def inviting_entity(self):
        raise NotImplementedError

    def invite(self, invitation, user):
        raise NotImplementedError

    def _invite(self, invitation, user):
        self.invite(invitation, user)
        invitation.is_accepted = True
        invitation.save()
        messages.success(self.request, self.success_msg)
        send_confirmation_email(invitation)

    def __call__(self, request, invitation_id, **kwargs):
        logging.warning("Don't use this view in more apps until it gets cleaned up.")
        # add the correct parameters to this instance
        self.request = request
        self.inv_id = invitation_id
        for k, v in kwargs.iteritems():
            if k in self.need:
                setattr(self, k, v)

        if request.GET.get('switch') == 'true':
            logout(request)
            return redirect_to_login(request.path)
        if request.GET.get('create') == 'true':
            logout(request)
            return HttpResponseRedirect(request.path)

        try:
            invitation = self.inv_type.get(invitation_id)
        except ResourceNotFound:
            messages.error(request, _("Sorry, it looks like your invitation has expired. "
                                      "Please check the invitation link you received and try again, or request a "
                                      "project administrator to send you the invitation again."))
            return HttpResponseRedirect(reverse("login"))
        if invitation.is_accepted:
            messages.error(request, _("Sorry, that invitation has already been used up. "
                                      "If you feel this is a mistake please ask the inviter for "
                                      "another invitation."))
            return HttpResponseRedirect(reverse("login"))

        self.validate_invitation(invitation)

        if invitation.is_expired:
            return HttpResponseRedirect(reverse("no_permissions"))

        context = self.added_context()
        if request.user.is_authenticated():
            is_invited_user = request.couch_user.username.lower() == invitation.email.lower()
            if self.is_invited(invitation, request.couch_user) and not request.couch_user.is_superuser:
                if is_invited_user:
                    # if this invite was actually for this user, just mark it accepted
                    messages.info(request, _("You are already a member of {entity}.").format(
                        entity=self.inviting_entity))
                    invitation.is_accepted = True
                    invitation.save()
                else:
                    messages.error(request, _("It looks like you are trying to accept an invitation for "
                                             "{invited} but you are already a member of {entity} with the "
                                             "account {current}. Please sign out to accept this invitation "
                                             "as another user.").format(
                                                 entity=self.inviting_entity,
                                                 invited=invitation.email,
                                                 current=request.couch_user.username,
                                             ))
                return HttpResponseRedirect(self.redirect_to_on_success)

            if not is_invited_user:
                messages.error(request, _("The invited user {invited} and your user {current} do not match!").format(
                    invited=invitation.email, current=request.couch_user.username))

            if request.method == "POST":
                couch_user = CouchUser.from_django_user(request.user)
                self._invite(invitation, couch_user)
                return HttpResponseRedirect(self.redirect_to_on_success)
            else:
                mobile_user = CouchUser.from_django_user(request.user).is_commcare_user()
                context.update({
                    'mobile_user': mobile_user,
                    "invited_user": invitation.email if request.couch_user.username != invitation.email else "",
                })
                return render(request, self.template, context)
        else:
            if request.method == "POST":
                form = NewWebUserRegistrationForm(request.POST)
                if form.is_valid():
                    # create the new user
                    user = activate_new_user(form)
                    user.save()
                    messages.success(request, _("User account for %s created!") % form.cleaned_data["email"])
                    self._invite(invitation, user)
                    authenticated = authenticate(username=form.cleaned_data["email"],
                                                 password=form.cleaned_data["password"])
                    if authenticated is not None and authenticated.is_active:
                        login(request, authenticated)
                    if isinstance(invitation, DomainInvitation):
                        return HttpResponseRedirect(reverse("domain_homepage", args=[invitation.domain]))
                    else:
                        return HttpResponseRedirect(reverse("homepage"))
            else:
                if isinstance(invitation, DomainInvitation):
                    if CouchUser.get_by_username(invitation.email):
                        return HttpResponseRedirect(reverse("login") + '?next=' +
                            reverse('domain_accept_invitation', args=[invitation.domain, invitation.get_id]))
                    domain = Domain.get_by_name(invitation.domain)
                    form = NewWebUserRegistrationForm(initial={
                        'email': invitation.email,
                        'hr_name': domain.display_name() if domain else invitation.domain,
                        'create_domain': False,
                    })
                else:
                    form = NewWebUserRegistrationForm(initial={'email': invitation.email})

        context.update({"form": form})
        return render(request, self.template, context)


def get_bulk_upload_form(context, context_key="bulk_upload"):
    return BulkUploadForm(
        context[context_key]['plural_noun'],
        context[context_key].get('action'),
        context_key + "_form"
    )


def sidebar_to_dropdown(sidebar_items, domain=None, current_url_name=None):
    """
    Formats sidebar_items as dropdown items
    Sample input:
        [(u'Application Users',
          [{'description': u'Create and manage users for CommCare and CloudCare.',
            'show_in_dropdown': True,
            'subpages': [{'title': <function commcare_username at 0x109869488>,
                          'urlname': 'edit_commcare_user'},
                         {'title': u'Bulk Upload',
                          'urlname': 'upload_commcare_users'},
                         {'title': 'Confirm Billing Information',],
            'title': u'Mobile Workers',
            'url': '/a/sravan-test/settings/users/commcare/'},
         (u'Project Users',
          [{'description': u'Grant other CommCare HQ users access
                            to your project and manage user roles.',
            'show_in_dropdown': True,
            'subpages': [{'title': u'Add Web User',
                          'urlname': 'invite_web_user'},
                         {'title': <function web_username at 0x10982a9b0>,
                          'urlname': 'user_account'},
                         {'title': u'My Information',
                          'urlname': 'domain_my_account'}],
            'title': <django.utils.functional.__proxy__ object at 0x106a5c790>,
            'url': '/a/sravan-test/settings/users/web/'}])]
    Sample output:
        [{'data_id': None,
          'html': None,
          'is_divider': False,
          'is_header': True,
          'title': u'Application Users',
          'url': None},
         {'data_id': None,
          'html': None,
          'is_divider': False,
          'is_header': False,
          'title': u'Mobile Workers',
          'url': '/a/sravan-test/settings/users/commcare/'},
         {'data_id': None,
          'html': None,
          'is_divider': False,
          'is_header': False,
          'title': u'Groups',
          'url': '/a/sravan-test/settings/users/groups/'},
         {'data_id': None,
          'html': None,
          'is_divider': False,
          'is_header': True,
          'title': u'Project Users',
          'url': None},]
    """
    dropdown_items = []
    more_items_in_sidebar = False
    for side_header, side_list in sidebar_items:
        dropdown_header = dropdown_dict(side_header, is_header=True)
        current_dropdown_items = []
        for side_item in side_list:
            show_in_dropdown = side_item.get("show_in_dropdown", False)
            if show_in_dropdown:
                second_level_dropdowns = subpages_as_dropdowns(
                    side_item.get('subpages', []), level=2, domain=domain)
                dropdown_item = dropdown_dict(
                    side_item['title'],
                    url=side_item['url'],
                    second_level_dropdowns=second_level_dropdowns,
                )
                current_dropdown_items.append(dropdown_item)
                first_level_dropdowns = subpages_as_dropdowns(
                    side_item.get('subpages', []), level=1, domain=domain
                )
                current_dropdown_items = current_dropdown_items + first_level_dropdowns
            else:
                more_items_in_sidebar = True
        if current_dropdown_items:
            dropdown_items.extend([dropdown_header] + current_dropdown_items)

    if more_items_in_sidebar and current_url_name:
        return dropdown_items + divider_and_more_menu(current_url_name)
    else:
        return dropdown_items


def subpages_as_dropdowns(subpages, level, domain=None):
    """
        formats subpages of a sidebar_item as 1st or 2nd level dropdown items
        depending on if level is 1 or 2 respectively
    """
    def is_dropdown(subpage):
        if subpage.get('show_in_dropdown', False) and level == 1:
            return subpage.get('show_in_first_level', False)
        elif subpage.get('show_in_dropdown', False) and level == 2:
            return not subpage.get('show_in_first_level', False)

    return [dropdown_dict(
            subpage['title'],
            url=reverse(subpage['urlname'], args=[domain]))
            for subpage in subpages if is_dropdown(subpage)]


def dropdown_dict(title, url=None, html=None,
                  is_header=False, is_divider=False, data_id=None,
                  second_level_dropdowns=[]):
    if second_level_dropdowns:
        return submenu_dropdown_dict(title, url, second_level_dropdowns)
    else:
        return main_menu_dropdown_dict(title, url=url, html=html,
                                       is_header=is_header,
                                       is_divider=is_divider,
                                       data_id=data_id,)


def main_menu_dropdown_dict(title, url=None, html=None,
                            is_header=False, is_divider=False, data_id=None,
                            second_level_dropdowns=[]):
    return {
        'title': title,
        'url': url,
        'html': html,
        'is_header': is_header,
        'is_divider': is_divider,
        'data_id': data_id,
    }


def submenu_dropdown_dict(title, url, menu):
    return {
        'title': title,
        'url': url,
        'is_second_level': True,
        'submenu': menu,
    }


def divider_and_more_menu(url):
    return [dropdown_dict('placeholder', is_divider=True),
            dropdown_dict(_('View All'), url=url)]
