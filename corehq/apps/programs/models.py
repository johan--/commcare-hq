from corehq.apps.groups.models import dt_no_Z_re
from dimagi.ext.couchdbkit import (
    Document,
    StringProperty,
    BooleanProperty,
    DateTimeProperty,
)
from datetime import datetime
from corehq.apps.products.models import Product, SQLProduct
from django.utils.translation import ugettext as _


class Program(Document):
    """
    A program, e.g. "hiv" or "tb"
    """
    domain = StringProperty()
    name = StringProperty()
    code = StringProperty()
    last_modified = DateTimeProperty()
    default = BooleanProperty(default=False)
    is_archived = BooleanProperty(default=False)

    @classmethod
    def wrap(cls, data):
        # If "Z" is missing because of the Aug 2014 migration, then add it.
        # cf. Group class
        last_modified = data.get('last_modified')
        if last_modified and dt_no_Z_re.match(last_modified):
            data['last_modified'] += 'Z'
        return super(Program, cls).wrap(data)

    def save(self, *args, **kwargs):
        self.last_modified = datetime.utcnow()

        return super(Program, self).save(*args, **kwargs)

    @classmethod
    def by_domain(cls, domain, wrap=True):
        """
        Gets all programs in a domain.
        """
        kwargs = dict(
            view_name='commtrack/programs',
            startkey=[domain],
            endkey=[domain, {}],
            include_docs=True
        )
        if wrap:
            return Program.view(**kwargs)
        else:
            return [row["doc"] for row in Program.view(wrap_doc=False, **kwargs)]

    @classmethod
    def default_for_domain(cls, domain):
        programs = cls.by_domain(domain)
        for p in programs:
            if p.default:
                return p

    def delete(self):
        # you cannot delete the default program
        if self.default:
            raise Exception(_('You cannot delete the default program'))

        default = Program.default_for_domain(self.domain)

        products = Product.by_program_id(
            self.domain,
            self._id,
            wrap=True
        )
        to_save = []

        for product in products:
            product['program_id'] = default._id
            to_save.append(product)

            # break up saving in case there are many products
            if len(to_save) > 500:
                Product.bulk_save(to_save)
                to_save = []

        Product.bulk_save(to_save)

        # bulk update sqlproducts
        SQLProduct.objects.filter(program_id=self._id).update(program_id=default._id)

        return super(Program, self).delete()

    def unarchive(self):
        """
        Unarchive a program, causing it (and its data) to show
        up in Couch and SQL views again.
        """
        self.is_archived = False
        self.save()

    @classmethod
    def get_by_code(cls, domain, code):
        result = cls.view("commtrack/program_by_code",
                          key=[domain, code],
                          include_docs=True,
                          limit=1).first()
        return result


