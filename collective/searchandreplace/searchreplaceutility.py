# -*- coding: us-ascii -*-

from Acquisition import aq_base
from Acquisition import aq_parent
from collective.searchandreplace import SearchAndReplaceMessageFactory as _
from collective.searchandreplace.interfaces import ISearchReplaceSettings
from plone.app.layout.navigation.defaultpage import isDefaultPage
from plone.app.textfield import RichTextValue
from plone.app.textfield.interfaces import IRichText
from plone.registry.interfaces import IRegistry
from Products.Archetypes.interfaces import ITextField
from Products.CMFCore.permissions import ModifyPortalContent
from Products.CMFCore.utils import getToolByName
from zope.component import getUtility
from zope.component.hooks import getSite
from zope.i18n import translate
from zope.schema import getFieldsInOrder
from zope.schema.interfaces import IText
from zope.schema.interfaces import ITextLine

import logging
import pkg_resources
import re

try:
    pkg_resources.get_distribution('plone.dexterity')
except pkg_resources.DistributionNotFound:
    HAS_DEXTERITY = False
else:
    HAS_DEXTERITY = True
    from plone.dexterity.utils import iterSchemata

logger = logging.getLogger('collective.searchreplace')
searchflags = re.DOTALL | re.UNICODE | re.MULTILINE
# List of text fields that are handled separately, instead of together with all
# text fields.  Note that 'title' is a string field, so we handle it
# separately, being the only string field that we want to change.  But we list
# it here to be on the safe side.
CUSTOM_HANDLED_TEXT_FIELDS = [
    'title',
]


def _to_unicode(s):
    assert isinstance(s, basestring)
    if not isinstance(s, unicode):
        s = s.decode('utf-8')
    return s


class SearchConfiguration(object):

    def __init__(self, context, find, **kwargs):
        self.cpath = context.getPhysicalPath()
        onlySearchableText = kwargs.get('onlySearchableText', True)
        self.replaceWith = kwargs.get('replaceWith', u'')
        self.doReplace = kwargs.get('doReplace', False)
        self.occurences = kwargs.get('occurences', None)
        self.maxResults = kwargs.get('maxResults', None)
        # Get items to search
        query = {'query': '/'.join(self.cpath)}
        ssf = kwargs.get('searchSubFolders', True)
        if context.isPrincipiaFolderish and not ssf:
            query['depth'] = 1
        container = aq_parent(context)
        if isDefaultPage(container, context) and ssf:
            query['query'] = '/'.join(container.getPhysicalPath())
        self.catalog_query_args = dict(path=query)
        if self.settings.restrict_searchable_types:
            self.catalog_query_args['portal_type'] = self.settings.enabled_types
        if onlySearchableText:
            self.catalog_query_args['SearchableText'] = u'*{0}*'.format(find)
        # Get Regex matcher
        mc = kwargs.get('matchCase', False)
        sflags = mc and searchflags or (searchflags | re.IGNORECASE)
        self.matcher = re.compile(find, sflags)
        memship = getToolByName(context, 'portal_membership')
        self.checkPermission = memship.checkPermission

    @property
    def settings(self):
        registry = getUtility(IRegistry)
        return registry.forInterface(ISearchReplaceSettings, check=False)


class SearchReplaceUtility(object):
    """ Search and replace utility. """

    # Permission to check before modifying content.
    permission = ModifyPortalContent

    def searchObjects(self, context, find, **kwargs):
        """ Search objects and optionally do a replace. """
        config = SearchConfiguration(context, find, **kwargs)
        catalog = getToolByName(context, 'portal_catalog')
        brains = catalog(**config.catalog_query_args)
        # Match objects
        results = []
        repl_count = 0
        occurences = config.occurences
        for b in brains:
            ipath = b.getPath()
            if not occurences or ipath in occurences:
                try:
                    obj = b.getObject()
                except (KeyError, AttributeError):
                    logger.warn('getObject failed for %s', ipath)
                    continue
                # Does the user have the modify permission on this object?
                if not config.checkPermission(self.permission, obj):
                    continue
                # If there is a filtered list of items, and it
                # is in the list, or if there is no filter
                # then process the item
                if config.doReplace:
                    # Do a replace
                    if occurences:
                        occurence = occurences[ipath]
                    else:
                        occurence = None
                    rep = replaceObject(config.matcher,
                                              obj,
                                              config.cpath,
                                              config.replaceWith,
                                              occurence,
                                              config.settings.update_modified)
                    if rep:
                        afterReplace(obj, find, config.replaceWith)
                        repl_count += rep
                elif not config.doReplace:
                    # Just find the matches and return info
                    result = searchObject(config.matcher, obj)
                    if result:
                        results += result
                    if config.maxResults is not None and len(results) > config.maxResults:
                        results = results[:config.maxResults]
                        break
        if config.doReplace:
            return repl_count
        else:
            return results


def afterReplace(obj, find, rtext):
    """Hook for doing things after a text has been replaced.

    - obj is the changed object
    - find is the found text
    - rtext is the replacement text

    By default, we will store a version in the CMFEditions repository.
    """
    repository = getToolByName(obj, 'portal_repository', None)
    if repository is None:
        return
    if obj.portal_type not in repository.getVersionableContentTypes():
        return
    comment = _(u'Replaced: ${old} -> ${new}',
                mapping={'old': find, 'new': rtext})
    comment = translate(comment, context=obj.REQUEST)
    repository.save(obj, comment=comment)


def replaceObject(matcher, obj, cpath, rtext, mobjs, update_modified):
    """ Replace text in objects """
    repl_count = 0
    # rtext is already unicode
    base_obj = aq_base(obj)
    if mobjs:
        # Replace only the objects specified in mobjs
        title_positions = mobjs.pop('title', None)
        if title_positions is not None:
            title = _to_unicode(base_obj.Title())
            count, new_text = replaceText(matcher,
                                       title,
                                       rtext,
                                       title_positions)
            if count:
                repl_count += count
                base_obj.setTitle(new_text)
        # Handle general text fields.
        for fieldname, positions in mobjs.items():
            text = getRawText(obj, fieldname)
            if text:
                count, new_text = replaceText(matcher,
                                           text,
                                           rtext,
                                           positions)
                if count:
                    repl_count += count
                    setTextField(obj, fieldname, new_text)
    else:
        # Replace all occurences
        try:
            title = _to_unicode(base_obj.Title())
        except AttributeError:
            # Title might be acquired from parent for some types, which
            # breaks now that we have stripped away the acquisition chain
            # with aq_base.
            title = u''
        count, new_text = replaceText(matcher,
                                   title,
                                   rtext,
                                   None)
        if count:
            repl_count += count
            base_obj.setTitle(new_text)
        text_fields = getTextFields(obj)
        for field in text_fields:
            text = getRawTextField(obj, field)
            if not text:
                continue
            count, new_text = replaceText(matcher,
                                       text,
                                       rtext,
                                       None)
            if count:
                repl_count += count
                setTextField(obj, field.__name__, new_text)

    # don't have to utf-8 encoding
    if repl_count:
        if update_modified:
            obj.reindexObject()
        else:
            site = getSite()
            catalog = getToolByName(site, 'portal_catalog')
            obj.reindexObject(idxs=catalog.indexes())
    return repl_count


def replaceText(matcher, text, rtext, indexes):
    """ Replace instances """
    newtext = ''
    mindex = 0
    repl_count = 0
    mobj = matcher.finditer(text)
    for x in mobj:
        start, end = x.span()
        if not indexes or start in indexes:
            newtext += text[mindex:start]
            newtext += rtext
            mindex = end
            repl_count += 1
    newtext += text[mindex:]
    return repl_count, newtext


def searchObject(matcher, obj):
    """ Find location of search strings """
    results = []
    path = '/'.join(obj.getPhysicalPath())
    base = aq_base(obj)
    try:
        title = _to_unicode(base.Title())
    except AttributeError:
        # Title might be acquired from parent for some types, which breaks
        # now that we have stripped away the acquisition chain with
        # aq_base.
        title = u''
    mobj = matcher.finditer(title)
    for x in mobj:
        start, end = x.span()
        results.append({
            'path': path,
            'url': obj.absolute_url(),
            'line': 'title',
            'pos': '%d' % start,
            'text': getLinePreview(title,
                                         start,
                                         end), })
    text_fields = getTextFields(obj)
    if text_fields:
        for field in text_fields:
            text = getRawTextField(obj, field)
            if not text:
                continue
            mobj = matcher.finditer(text)
            for x in mobj:
                start, end = x.span()
                results.append({
                    'path': path,
                    'url': obj.absolute_url(),
                    'line': '%s %d' % (field.__name__,
                                       getLineNumber(text, start)),
                    'pos': '%d' % start,
                    'text': getLinePreview(text,
                                                 start,
                                                 end), })
    return results


def getTextFields(obj):
    # Get all text fields, except ones that are handled separately.
    text_fields = []
    if getattr(aq_base(obj), 'Schema', None):
        # Archetypes
        for field in obj.Schema().values():
            if field.__name__ in CUSTOM_HANDLED_TEXT_FIELDS:
                continue
            if not ITextField.providedBy(field):
                continue
            text_fields.append(field)
    elif HAS_DEXTERITY:
        # Dexterity
        for schemata in iterSchemata(obj):
            fields = getFieldsInOrder(schemata)
            for name, field in fields:
                if name in CUSTOM_HANDLED_TEXT_FIELDS:
                    continue
                if IRichText.providedBy(field):
                    text_fields.append(field)
                    continue
                # ITextLine inherits from IText.
                # We want to replace in texts, but not textlines.
                # Maybe this can be made configurable.
                if ITextLine.providedBy(field):
                    continue
                if not IText.providedBy(field):
                    continue
                text_fields.append(field)
    return text_fields


def setTextField(obj, fieldname, text):
    obj_base = aq_base(obj)
    if getattr(obj_base, 'Schema', None):
        # Archetypes
        field = obj_base.getField(fieldname)
        if field is None:
            logger.warn('Field %s not found for %s',
                        fieldname, obj.getId())
            return
        field.set(obj, text)
    else:
        # Dexterity
        field = getField(obj_base, fieldname)
        if field is None:
            logger.warn('Field %s not found for %s',
                        fieldname, obj.getId())
            return
        if IRichText.providedBy(field):
            # Get mimetype from old value.
            old = field.get(obj)
            if old is None:
                text = RichTextValue(text)
            else:
                text = RichTextValue(
                    text, old.mimeType, old.outputMimeType)
        field.set(obj, text)


def getLineNumber(text, index):
    return text.count('\n', 0, index) + 1


def getLinePreview(text, start, end):
    sindex = text[:start].rfind('\n')
    if -1 == sindex:
        sindex = 0
    eindex = text[end:].find('\n')
    if -1 == eindex:
        eindex = None
    else:
        eindex += end
    return (text[sindex:start],
            text[start:end],
            text[end:eindex])


def getField(obj, fieldname):
    obj = aq_base(obj)
    if getattr(obj, 'Schema', None):
        # Archetypes
        return obj.getField(fieldname)
    # Dexterity
    for schemata in iterSchemata(obj):
        fields = getFieldsInOrder(schemata)
        for name, field in fields:
            if name == fieldname:
                return field


def getRawTextField(obj, field):
    text = None
    obj = aq_base(obj)
    if hasattr(field, 'getRaw'):
        # Archetypes
        baseunit = field.getRaw(obj, raw=True)
        if isinstance(baseunit, tuple):
            #  LinesField
            text = '\n'.join(baseunit)
        elif isinstance(baseunit.raw, unicode):
            text = baseunit.raw
        else:
            text = _to_unicode(field.getRaw(obj))
    else:
        # Dexterity
        baseunit = field.get(obj)
        if baseunit is None:
            text = u''
        else:
            # Rich text has a raw attribute, plain text simply has text
            # (unicode).
            text = getattr(baseunit, 'raw', baseunit)
    return text


def getRawText(obj, fieldname='text'):
    field = getField(obj, fieldname)
    if field is None:
        return u''
    return getRawTextField(obj, field)
