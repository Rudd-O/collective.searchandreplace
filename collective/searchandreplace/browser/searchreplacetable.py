# -*- coding: us-ascii -*-
from collective.searchandreplace.interfaces import ISearchReplaceUtility
from collective.searchandreplace.interfaces import ISearchReplaceSettings
from zope.component import getUtility
from plone.registry.interfaces import IRegistry
from zope.publisher.browser import BrowserView


class SearchReplaceTable(BrowserView):
    """ View class for search and replace preivew table widget."""

    def maximum_text_characters(self):
        registry = getUtility(IRegistry)
        settings = registry.forInterface(ISearchReplaceSettings, check=False)
        return settings.maximum_text_characters

    def getItems(self):
        """ Get preview items """
        # Set search parameters
        srutil = getUtility(ISearchReplaceUtility)
        stext = None
        if 'form.findWhat' in self.request:
            stext = self.request['form.findWhat']
        if not stext:
            return []
        if 'form.searchSubfolders' in self.request:
            subfolders = True
        else:
            subfolders = False
        if 'form.matchCase' in self.request:
            mcase = True
        else:
            mcase = False
        if 'form.maxResults' in self.request and self.request[
                'form.maxResults']:
            maxResults = int(self.request['form.maxResults'])
        else:
            maxResults = None
        onlySearchableText = 'form.onlySearchableText' in self.request
        # Get search results
        results = srutil.searchObjects(
            self.context,
            stext,
            searchSubFolders=subfolders,
            matchCase=mcase,
            maxResults=maxResults,
            onlySearchableText=onlySearchableText,
        )
        return results

    def getRelativePath(self, path):
        """ Get a relative path """
        cpath = '/'.join(self.context.getPhysicalPath())
        rpath = path[len(cpath):]
        if rpath:
            rpath = '.' + rpath
        else:
            rpath = './'
        return rpath
