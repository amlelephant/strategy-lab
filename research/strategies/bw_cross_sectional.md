## The claim

Score every company on quality, valuation and leverage against the current peer group. Each company is scored on these different methods: 

- **percentile** - fraction of peers this company beats. Rank-based, so one
  absurd P/E can't move anybody else's score.
- **zscore** - median-centred, standard-deviation-scaled. Keeps the size of a
  gap, not just its order, and inherits every outlier along with it.
- **minmax** - linear between the best and worst peer in the cross-section.
  Two extreme companies decide everybody's score. Kept specifically so that
  claim is demonstrable rather than folklore. Set `method="minmax"` and
  watch one outlier move the whole ranking.

Weights are the original's: 45% quality, 45% valuation, 10% risk.

## Methods

Valuation of a company is the foundational strategy in traditional hedge funds. One of the greatest men to do this was Warren Buffet, whose methods inspired the creation of this algorithim. 

### Quality

A companies quality is how good it is at producing capital flow. A company that is valued cheaply could be a completely horrid company in producing cashflow which is why this is absolutely nescessary in determining if a company can convert a cheap stock price into a market beating return on stock price. 

- **Operating Margin** - Tells us what fraction of every sales dollar the buisness keeps after running itself, before interest and taxes. We look for it to be both high and stable which demonstrates pricing power and cost discipline. 
- **FCF Margin** - What fraction of sales actually turns into spendable cash after capex. This acts as a check on operating margin in order to make sure the healthy operating margin is substantiated by liquid assets. 
- **ROIC** - How efficient a company is at turning capex into profit. If a company is able to have a high return on invested capital then their upside growth can likely be unleashed with an increase in investment. 
- **Revenue Growth** - Is the buisness actually getting bigger. This is very important to put into the context of ROIC of course, but it still will help rule out the lower end of companies that show good margins yet still manage to have a decreasing revenue.

### Valuation

This is how good of a deal we are actually getting on the buisness. Tech companies look amazing on paper because of their insanely high operating margins, but with the context of valuation we are able determine if we actually get a worth while deal on the company.

- **FCF Yeild** - How much cash the buisness throws off per dollar you're paying for it. A high yeild would indicate that it is producing a lot of spendable cash that it is able to share with the investors. Basically how much you will be rewarded for investing. 
- **EV/EBITDA** - Allows you to compare the actual value of a company with debt on an equal footing. This is beneficial over P/E, yet P/E is still nescessary.
- **P/E** - Price per dollar of net income. One of the most used multiples that allows us to assess how a companies stock price compares to it's earnings. Needs to be industry neutralized. 

### Risk

A company can look great on paper but if it is drowning in debt you will only ever get the total value of the compaies assets.

- **Net Debt/EBITDA** - How many years of current cash earnings it would take to pay off the companie's net debt. High debt is incredably risky in an industry that is exposed to frequent swings. Even if a company is incredably profitable, if it is drowning in debt they likely won't be able to make it through hard times. 

## Future Improvements

I believe a significant weakness of this model lies in valuation. I believe we do a great job of finding good companies but I think that there should be an inclusion of somethign more technical for price breakdown such as DCF valuation. Valuation is just as important as quality since a good company with a high valuation could grow very slow. 