/**
 * Help content registry — defines sections for each page's help drawer.
 * All values are i18n keys (not raw text).
 */
export const HELP_PAGES = {
    scenarios: {
        titleKey: 'help.scenarios.title',
        overviewKey: 'help.scenarios.overview',
        sections: [
            { titleKey: 'help.scenarios.templates.title', contentKey: 'help.scenarios.templates.content' },
            { titleKey: 'help.scenarios.dialogues.title', contentKey: 'help.scenarios.dialogues.content' },
            { titleKey: 'help.scenarios.safety.title', contentKey: 'help.scenarios.safety.content' },
        ],
        tipsKey: 'help.scenarios.tips',
    },
    knowledge: {
        titleKey: 'help.knowledge.title',
        overviewKey: 'help.knowledge.overview',
        sections: [
            { titleKey: 'help.knowledge.articles.title', contentKey: 'help.knowledge.articles.content' },
            { titleKey: 'help.knowledge.sources.title', contentKey: 'help.knowledge.sources.content' },
            { titleKey: 'help.knowledge.scraper.title', contentKey: 'help.knowledge.scraper.content' },
            { titleKey: 'help.knowledge.watched.title', contentKey: 'help.knowledge.watched.content' },
        ],
        tipsKey: 'help.knowledge.tips',
    },
    prompts: {
        titleKey: 'help.prompts.title',
        overviewKey: 'help.prompts.overview',
        sections: [
            { titleKey: 'help.prompts.versions.title', contentKey: 'help.prompts.versions.content' },
            { titleKey: 'help.prompts.ab.title', contentKey: 'help.prompts.ab.content' },
        ],
        tipsKey: 'help.prompts.tips',
    },
    sandbox: {
        titleKey: 'help.sandbox.title',
        overviewKey: 'help.sandbox.overview',
        sections: [
            { titleKey: 'help.sandbox.chat.title', contentKey: 'help.sandbox.chat.content' },
            { titleKey: 'help.sandbox.regression.title', contentKey: 'help.sandbox.regression.content' },
            { titleKey: 'help.sandbox.patterns.title', contentKey: 'help.sandbox.patterns.content' },
            { titleKey: 'help.sandbox.phrases.title', contentKey: 'help.sandbox.phrases.content' },
            { titleKey: 'help.sandbox.starters.title', contentKey: 'help.sandbox.starters.content' },
        ],
        tipsKey: 'help.sandbox.tips',
    },
    tools: {
        titleKey: 'help.tools.title',
        overviewKey: 'help.tools.overview',
        sections: [
            { titleKey: 'help.tools.overrides.title', contentKey: 'help.tools.overrides.content' },
        ],
        tipsKey: 'help.tools.tips',
    },
};
